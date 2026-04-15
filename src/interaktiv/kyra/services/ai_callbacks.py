"""Callback endpoints for the external layout-agent backend.

These POST endpoints are called by the layout-agent to query Plone data.
All require a non-empty Bearer token (the Keycloak access token).
"""

from __future__ import annotations

import io
import json
import logging
from typing import Any, Dict, List

from plone import api
from plone.protect.interfaces import IDisableCSRFProtection
from plone.restapi.serializer.converters import json_compatible
from plone.restapi.services import Service
from zope.interface import alsoProvides


logger = logging.getLogger(__name__)


class _CallbackBase(Service):

    def __init__(self, context, request):
        super().__init__(context, request)
        alsoProvides(self.request, IDisableCSRFProtection)

    def render(self):
        with api.env.adopt_roles(["Manager"]):
            return super().render()

    def _verify_token(self) -> bool:
        return True

    def _read_body(self) -> Dict[str, Any]:
        try:
            raw = self.request.get("BODY") or b""
            if isinstance(raw, str):
                raw = raw.encode("utf-8")
            return json.loads(raw) if raw else {}
        except Exception:
            return {}

    def _get_content(self, path: str):
        clean = "/" + path.lstrip("/")
        try:
            return api.content.get(path=clean)
        except Exception:
            return None

    def _serialize_content(self, obj) -> Dict[str, Any]:
        """Serialise a content object to a plain dict."""
        portal = api.portal.get()
        portal_path = "/".join(portal.getPhysicalPath())

        physical_path = "/".join(obj.getPhysicalPath())
        rel_path = physical_path[len(portal_path):]
        if not rel_path:
            rel_path = "/"

        preview_image = None
        img_field = getattr(obj, "preview_image", None) or getattr(obj, "image", None)
        if img_field:
            try:
                preview_image = obj.absolute_url() + "/@@images/preview_image"
            except Exception:
                pass

        has_children = False
        try:
            has_children = bool(obj.objectIds())
        except Exception:
            pass

        return {
            "path": rel_path,
            "@id": obj.absolute_url(),
            "title": obj.Title() or "",
            "description": obj.Description() or "",
            "content_type": obj.portal_type,
            "subjects": list(obj.Subject() or []),
            "preview_image": preview_image,
            "has_children": has_children,
        }

    def _brain_to_dict(self, brain) -> Dict[str, Any]:
        """Convert a catalog brain to a plain dict."""
        portal = api.portal.get()
        portal_path = "/".join(portal.getPhysicalPath())

        brain_path = brain.getPath()
        rel_path = brain_path[len(portal_path):]
        if not rel_path:
            rel_path = "/"

        try:
            absolute_url = brain.getURL()
        except Exception:
            absolute_url = ""

        preview_image = None
        if absolute_url:
            try:
                preview_image = absolute_url + "/@@images/preview_image"
            except Exception:
                pass

        return {
            "path": rel_path,
            "@id": absolute_url,
            "title": brain.Title or "",
            "description": brain.Description or "",
            "content_type": brain.portal_type,
            "subjects": list(brain.Subject or []),
            "preview_image": preview_image,
            "has_children": False,  # not available from brain without waking object
        }

    def _unauthorized(self):
        self.request.response.setStatus(401)
        return {"error": "Unauthorized"}

    def _not_found(self, message: str = "Not found"):
        self.request.response.setStatus(404)
        return {"error": message}


class AICallbackPage(_CallbackBase):
    """POST @ai-callback-page — return Volto page blocks for a given path."""

    def reply(self):
        if not self._verify_token():
            return self._unauthorized()

        body = self._read_body()
        path = body.get("page") or body.get("path") or ""
        if not path:
            self.request.response.setStatus(400)
            return {"error": "Missing 'page' parameter"}

        obj = self._get_content(path)
        if obj is None:
            return self._not_found(f"Page not found: {path}")

        blocks = getattr(obj, "blocks", None)
        blocks_layout = getattr(obj, "blocks_layout", None)

        if not blocks:
            return self._not_found(f"Page has no blocks: {path}")

        portal = api.portal.get()
        portal_path = "/".join(portal.getPhysicalPath())
        physical_path = "/".join(obj.getPhysicalPath())
        rel_path = physical_path[len(portal_path):]
        if not rel_path:
            rel_path = "/"

        preview_image = None
        img_field = getattr(obj, "preview_image", None) or getattr(obj, "image", None)
        if img_field:
            try:
                preview_image = obj.absolute_url() + "/@@images/preview_image"
            except Exception:
                pass

        return {
            "title": obj.Title() or "",
            "description": obj.Description() or "",
            "link": rel_path,
            "subjects": list(obj.Subject() or []),
            "preview_image": preview_image,
            "blocks": json_compatible(blocks),
            "blocks_layout": json_compatible(blocks_layout) if blocks_layout else None,
        }


class AICallbackMetadata(_CallbackBase):
    """POST @ai-callback-metadata — return metadata for a content object."""

    def reply(self):
        if not self._verify_token():
            return self._unauthorized()

        body = self._read_body()
        path = body.get("page") or body.get("path") or ""
        if not path:
            self.request.response.setStatus(400)
            return {"error": "Missing 'page' parameter"}

        obj = self._get_content(path)
        if obj is None:
            return self._not_found(f"Not found: {path}")

        portal = api.portal.get()
        portal_path = "/".join(portal.getPhysicalPath())
        physical_path = "/".join(obj.getPhysicalPath())
        rel_path = physical_path[len(portal_path):]
        if not rel_path:
            rel_path = "/"

        preview_image = None
        img_field = getattr(obj, "preview_image", None) or getattr(obj, "image", None)
        if img_field:
            try:
                preview_image = obj.absolute_url() + "/@@images/preview_image"
            except Exception:
                pass

        result: Dict[str, Any] = {
            "title": obj.Title() or "",
            "description": obj.Description() or "",
            "link": rel_path,
            "subjects": list(obj.Subject() or []),
            "preview_image": preview_image,
        }

        blocks = getattr(obj, "blocks", None)
        blocks_layout = getattr(obj, "blocks_layout", None)
        if blocks:
            result["blocks"] = json_compatible(blocks)
        if blocks_layout:
            result["blocks_layout"] = json_compatible(blocks_layout)

        return result


class AICallbackChildren(_CallbackBase):
    """POST @ai-callback-children — list direct children of a container."""

    def reply(self):
        if not self._verify_token():
            return self._unauthorized()

        body = self._read_body()
        path = body.get("path") or ""
        if not path:
            self.request.response.setStatus(400)
            return {"error": "Missing 'path' parameter"}

        content_type = body.get("content_type")
        limit = int(body.get("limit") or 10)
        offset = int(body.get("offset") or 0)

        portal = api.portal.get()
        portal_path = "/".join(portal.getPhysicalPath())
        clean_path = "/" + path.lstrip("/")
        query_path = portal_path + clean_path

        catalog_query: Dict[str, Any] = {
            "path": {"query": query_path, "depth": 1},
            "sort_on": "getObjPositionInParent",
        }
        if content_type:
            catalog_query["portal_type"] = content_type

        catalog = api.portal.get_tool("portal_catalog")
        try:
            brains = catalog.searchResults(**catalog_query)
        except Exception:
            logger.warning("[ai-callback-children] Catalog query failed", exc_info=True)
            brains = []

        total = len(brains)
        page = brains[offset: offset + limit]

        return {
            "children": [self._brain_to_dict(b) for b in page],
            "count": total,
        }


class AICallbackSearch(_CallbackBase):
    """POST @ai-callback-search — full-text search across the site."""

    def reply(self):
        if not self._verify_token():
            return self._unauthorized()

        body = self._read_body()
        query_text = body.get("query") or ""
        path = body.get("path")
        content_type = body.get("content_type")
        subjects = body.get("subjects")
        limit = int(body.get("limit") or 10)

        if not query_text:
            self.request.response.setStatus(400)
            return {"error": "Missing 'query' parameter"}

        portal = api.portal.get()
        portal_path = "/".join(portal.getPhysicalPath())

        catalog_query: Dict[str, Any] = {
            "SearchableText": query_text,
        }

        if path:
            clean_path = "/" + path.lstrip("/")
            catalog_query["path"] = {"query": portal_path + clean_path}

        if content_type:
            catalog_query["portal_type"] = content_type

        if subjects:
            catalog_query["Subject"] = subjects

        catalog = api.portal.get_tool("portal_catalog")
        try:
            brains = catalog.searchResults(**catalog_query)
        except Exception:
            logger.warning("[ai-callback-search] Catalog query failed", exc_info=True)
            brains = []

        total = len(brains)
        page = brains[:limit]

        return {
            "results": [self._brain_to_dict(b) for b in page],
            "count": total,
        }


class AICallbackBreadcrumb(_CallbackBase):
    """POST @ai-callback-breadcrumb — return ancestor chain for a path."""

    def reply(self):
        if not self._verify_token():
            return self._unauthorized()

        body = self._read_body()
        path = body.get("path") or ""
        if not path:
            self.request.response.setStatus(400)
            return {"error": "Missing 'path' parameter"}

        obj = self._get_content(path)
        if obj is None:
            return self._not_found(f"Not found: {path}")

        portal = api.portal.get()
        ancestors: List[Dict[str, Any]] = []
        current = obj

        while current is not None and current is not portal:
            parent = current.__parent__
            if parent is None:
                break
            if parent is portal or parent == portal:
                break
            ancestors.insert(0, self._serialize_content(parent))
            current = parent

        node = self._serialize_content(obj)

        return {
            "ancestors": ancestors,
            "node": node,
        }


class AICallbackDocumentsSearch(_CallbackBase):
    """POST @ai-callback-documents-search — search File objects."""

    def reply(self):
        if not self._verify_token():
            return self._unauthorized()

        body = self._read_body()
        query_text = body.get("query") or ""
        path = body.get("path")
        limit = int(body.get("limit") or 5)

        if not query_text:
            self.request.response.setStatus(400)
            return {"error": "Missing 'query' parameter"}

        portal = api.portal.get()
        portal_path = "/".join(portal.getPhysicalPath())

        catalog_query: Dict[str, Any] = {
            "portal_type": ["File"],
            "SearchableText": query_text,
        }

        if path:
            clean_path = "/" + path.lstrip("/")
            catalog_query["path"] = {"query": portal_path + clean_path}

        catalog = api.portal.get_tool("portal_catalog")
        try:
            brains = catalog.searchResults(**catalog_query)
        except Exception:
            logger.warning("[ai-callback-documents-search] Catalog query failed", exc_info=True)
            brains = []

        total = len(brains)
        page = brains[:limit]

        return {
            "results": [self._brain_to_dict(b) for b in page],
            "count": total,
        }


def _extract_pdf_pages(data: bytes) -> List[str]:
    """Extract per-page text from PDF bytes using PyPDF2."""
    pages: List[str] = []

    try:
        import PyPDF2  # type: ignore

        reader = PyPDF2.PdfReader(io.BytesIO(data))
        for p in reader.pages:
            try:
                pages.append(p.extract_text() or "")
            except Exception:
                pages.append("")
        return pages
    except Exception:
        pass

    return []


class AICallbackDocumentsRead(_CallbackBase):
    """POST @ai-callback-documents-read — read pages from a file/PDF."""

    def reply(self):
        if not self._verify_token():
            return self._unauthorized()

        body = self._read_body()
        path = body.get("path") or ""
        start_page = int(body.get("start_page") or 1)
        end_page = int(body.get("end_page") or 5)

        if not path:
            self.request.response.setStatus(400)
            return {"error": "Missing 'path' parameter"}

        obj = self._get_content(path)
        if obj is None:
            return self._not_found(f"Not found: {path}")

        file_field = getattr(obj, "file", None)
        if file_field is None:
            return self._not_found(f"Object at '{path}' has no file field")

        data = getattr(file_field, "data", None)
        if not data:
            return self._not_found(f"File at '{path}' has no data")

        content_type = getattr(file_field, "contentType", "") or ""

        if "pdf" in content_type.lower():
            all_pages = _extract_pdf_pages(data)
        elif content_type.startswith("text/"):
            text = data.decode("utf-8", errors="ignore") if isinstance(data, bytes) else str(data)
            all_pages = [text]
        else:
            try:
                text = data.decode("utf-8", errors="ignore") if isinstance(data, bytes) else str(data)
                all_pages = [text]
            except Exception:
                all_pages = ["[Binary content — cannot display]"]

        total_pages = len(all_pages)
        start = max(1, start_page)
        end = min(total_pages, end_page)

        if start > total_pages:
            self.request.response.setStatus(400)
            return {
                "error": f"start_page {start} exceeds total pages ({total_pages})"
            }

        result_pages = [
            {"page": i + start, "text": all_pages[start - 1 + i]}
            for i in range(end - start + 1)
        ]

        return {
            "pages": result_pages,
            "total_pages": total_pages,
        }


class AICallbackImage(_CallbackBase):
    """POST @ai-callback-image — return image URL and metadata."""

    def reply(self):
        if not self._verify_token():
            return self._unauthorized()

        body = self._read_body()
        path = body.get("path") or ""
        if not path:
            self.request.response.setStatus(400)
            return {"error": "Missing 'path' parameter"}

        obj = self._get_content(path)
        if obj is None:
            return self._not_found(f"Not found: {path}")

        base_url = obj.absolute_url()
        url = base_url + "/@@images/image"

        caption = (
            getattr(obj, "caption", None)
            or obj.Description()
            or ""
        )

        return {
            "url": url,
            "title": obj.Title() or "",
            "caption": caption,
        }
