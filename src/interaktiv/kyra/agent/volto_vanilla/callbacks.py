"""Client-side callback adapter for Volto-backed read operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from interaktiv.kyra.agent.volto_vanilla.converter import volto_to_page_state
from interaktiv.kyra.agent.volto_vanilla.engine import EngineResult
from interaktiv.kyra.agent.volto_vanilla.schema import PageState


def _err(code: str, message: str, **data: Any) -> EngineResult:
    return EngineResult(ok=False, code=code, message=message, data=data or None)


def _ok(code: str, message: str, data: Any = None) -> EngineResult:
    return EngineResult(ok=True, code=code, message=message, data=data)


class CallbackEndpoints(BaseModel):
    """Callback URLs supplied by the client at conversation creation time.

    All callbacks are invoked with ``POST`` and a JSON body. The callback access
    token is sent as ``Authorization: Bearer <token>`` and is never exposed to
    the LLM.
    """

    model_config = ConfigDict(extra="forbid")

    get_page: str | None = Field(
        default=None,
        description="Return a page object for a page/link.",
    )
    get_metadata: str | None = Field(
        default=None,
        description=(
            "Return a page object for metadata extraction. Falls back to "
            "get_page when omitted."
        ),
    )
    list_children: str | None = Field(
        default=None,
        description="Return direct child content for a path.",
    )
    search_content: str | None = Field(
        default=None,
        description="Return content search results.",
    )
    get_breadcrumb: str | None = Field(
        default=None,
        description="Return ancestors and optionally the node for a path.",
    )
    search_documents: str | None = Field(
        default=None,
        description="Return matching document chunks.",
    )
    read_document_pages: str | None = Field(
        default=None,
        description="Return page text for a document path/page range.",
    )
    view_image: str | None = Field(
        default=None,
        description="Return an image URL or multimodal image payload.",
    )

    def has_any(self) -> bool:
        return any(getattr(self, name) for name in type(self).model_fields)


def _unwrap_page_payload(raw: Any) -> Any:
    """Accept either raw Volto or a small response envelope."""
    if isinstance(raw, dict):
        for key in ("state", "page", "volto", "data", "result"):
            value = raw.get(key)
            if isinstance(value, dict) and "blocks" in value:
                return value
    return raw


def _extract_items(raw: Any, keys: tuple[str, ...]) -> list[Any]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for key in keys:
            value = raw.get(key)
            if isinstance(value, list):
                return value
        data = raw.get("data")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in keys:
                value = data.get(key)
                if isinstance(value, list):
                    return value
    return []


def _first_string(raw: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = raw.get(key)
        if isinstance(value, str):
            return value
    return ""


def _extract_href(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return _first_string(value[0], "@id", "url", "path", "link")
    if isinstance(value, dict):
        return _first_string(value, "@id", "url", "path", "link")
    return ""


def _summarize_content(raw: Any) -> dict[str, Any]:
    """Normalize a callback content item into the shape the agent tools use."""
    if not isinstance(raw, dict):
        return {"title": str(raw), "path": "", "content_type": ""}

    if "blocks" in raw and "blocks_layout" in raw:
        try:
            state = volto_to_page_state(raw)
        except Exception:
            state = None
        if state is not None:
            meta = state.metadata
            result: dict[str, Any] = {
                "path": meta.link or _first_string(raw, "@id", "path", "url"),
                "title": meta.title,
                "content_type": _first_string(
                    raw, "content_type", "portal_type", "@type"
                )
                or "Document",
            }
            if meta.description:
                result["description"] = meta.description
            if meta.subjects:
                result["subjects"] = meta.subjects
            if meta.preview_image:
                result["preview_image"] = meta.preview_image
            if meta.start is not None:
                result["start"] = meta.start.isoformat()
            if meta.end is not None:
                result["end"] = meta.end.isoformat()
            return result

    path = _first_string(raw, "path", "link", "@id", "url", "getURL")
    title = _first_string(raw, "title", "Title", "name", "id")
    content_type = _first_string(raw, "content_type", "portal_type", "@type")
    result = {
        "path": path,
        "title": title,
        "content_type": content_type,
    }
    description = _first_string(raw, "description", "Description")
    if description:
        result["description"] = description
    subjects = raw.get("subjects") or raw.get("Subject")
    if isinstance(subjects, list):
        result["subjects"] = subjects
    preview_image = _extract_href(raw.get("preview_image")) or _first_string(
        raw, "preview_image_url", "image_url", "image"
    )
    if preview_image:
        result["preview_image"] = preview_image
    for date_key in ("published", "created", "modified", "start", "end"):
        value = raw.get(date_key)
        if value:
            result[date_key] = value
    if bool(raw.get("has_children")):
        result["has_children"] = True
    return result


@dataclass
class CallbackVoltoClient:
    """HTTP adapter for client-provided callbacks."""

    endpoints: CallbackEndpoints
    access_token: str
    timeout_seconds: float = 15.0
    transport: httpx.AsyncBaseTransport | None = None
    _page_cache: dict[str, PageState] = field(default_factory=dict)

    async def _post(
        self, url: str | None, payload: dict[str, Any]
    ) -> Any | EngineResult:
        if not url:
            return _err(
                "read_not_configured",
                "The requested site read is not available.",
            )
        headers = {"Authorization": f"Bearer {self.access_token}"}
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as exc:
            return _err(
                "site_read_http_error",
                f"The requested site read failed with HTTP {exc.response.status_code}.",
                status_code=exc.response.status_code,
            )
        except httpx.HTTPError as exc:
            return _err("site_read_failed", f"The requested site read failed: {exc}.")
        except ValueError as exc:
            return _err(
                "site_read_invalid_response",
                f"The requested site read returned an invalid response: {exc}.",
            )

    async def fetch_page_state(self, page: str) -> PageState | EngineResult:
        cached = self._page_cache.get(page)
        if cached is not None:
            return cached

        raw = await self._post(self.endpoints.get_page, {"page": page})
        if isinstance(raw, EngineResult):
            return raw

        payload = _unwrap_page_payload(raw)
        if not isinstance(payload, dict):
            return _err(
                "invalid_volto",
                "The requested site read did not return a usable page.",
            )
        try:
            state = volto_to_page_state(payload)
        except Exception as exc:
            return _err("invalid_page", f"Could not convert the requested page: {exc}")
        self._page_cache[page] = state
        return state

    async def fetch_metadata(self, page: str) -> EngineResult:
        if self.endpoints.get_metadata:
            raw = await self._post(self.endpoints.get_metadata, {"page": page})
            if isinstance(raw, EngineResult):
                return raw
            payload = _unwrap_page_payload(raw)
            if isinstance(payload, dict) and "blocks" in payload:
                try:
                    state = volto_to_page_state(payload)
                except Exception as exc:
                    return _err(
                        "invalid_page",
                        f"Could not convert the requested page: {exc}",
                    )
                self._page_cache[page] = state
                return _ok(
                    "metadata",
                    f"Metadata for '{page}'.",
                    data=state.metadata.model_dump(),
                )
            return _err(
                "invalid_page",
                "The requested site read did not return a usable page.",
            )

        state = await self.fetch_page_state(page)
        if isinstance(state, EngineResult):
            return state
        return _ok(
            "metadata", f"Metadata for '{page}'.", data=state.metadata.model_dump()
        )

    async def list_children(
        self,
        *,
        path: str,
        content_type: str | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> dict[str, Any]:
        raw = await self._post(
            self.endpoints.list_children,
            {
                "path": path,
                "content_type": content_type,
                "limit": limit,
                "offset": offset,
            },
        )
        if isinstance(raw, EngineResult):
            return raw.model_dump()
        items = _extract_items(raw, ("children", "items", "results"))
        return {
            "children": [_summarize_content(item) for item in items],
            "count": len(items),
        }

    async def search_content(
        self,
        *,
        query: str | None = None,
        path: str | None = None,
        content_type: str | None = None,
        subjects: list[str] | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        raw = await self._post(
            self.endpoints.search_content,
            {
                "query": query,
                "path": path,
                "content_type": content_type,
                "subjects": subjects,
                "limit": limit,
            },
        )
        if isinstance(raw, EngineResult):
            return raw.model_dump()
        items = _extract_items(raw, ("results", "items", "children"))
        return {
            "results": [_summarize_content(item) for item in items],
            "count": len(items),
        }

    async def get_breadcrumb(self, *, path: str) -> dict[str, Any]:
        raw = await self._post(self.endpoints.get_breadcrumb, {"path": path})
        if isinstance(raw, EngineResult):
            return raw.model_dump()
        if isinstance(raw, dict):
            ancestors = _extract_items(raw, ("ancestors", "items"))
            result: dict[str, Any] = {
                "ancestors": [_summarize_content(item) for item in ancestors]
            }
            node = raw.get("node")
            if node is not None:
                result["node"] = _summarize_content(node)
            return result
        items = _extract_items(raw, ("ancestors", "items"))
        return {"ancestors": [_summarize_content(item) for item in items]}

    async def search_documents(
        self,
        *,
        query: str,
        path: str | None = None,
        limit: int = 5,
    ) -> Any:
        raw = await self._post(
            self.endpoints.search_documents,
            {"query": query, "path": path, "limit": limit},
        )
        if isinstance(raw, EngineResult):
            return raw.model_dump()
        return raw

    async def read_document_pages(
        self,
        *,
        path: str,
        start_page: int = 1,
        end_page: int = 5,
    ) -> Any:
        raw = await self._post(
            self.endpoints.read_document_pages,
            {"path": path, "start_page": start_page, "end_page": end_page},
        )
        if isinstance(raw, EngineResult):
            return raw.model_dump()
        return raw

    async def view_image(self, *, path: str) -> list[dict[str, Any]]:
        raw = await self._post(self.endpoints.view_image, {"path": path})
        if isinstance(raw, EngineResult):
            return [{"type": "text", "text": raw.message}]
        if isinstance(raw, list):
            return raw
        if isinstance(raw, dict):
            caption = _first_string(raw, "caption", "title", "Title") or path
            url = _first_string(raw, "url", "image_url", "@id") or _extract_href(
                raw.get("image")
            )
            if url:
                return [
                    {"type": "text", "text": caption},
                    {"type": "image", "url": url},
                ]
            return [{"type": "text", "text": str(raw)}]
        return [{"type": "text", "text": str(raw)}]
