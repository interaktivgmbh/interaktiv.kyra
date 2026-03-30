"""Agent tools — all site browsing and layout editing tools in one place."""

from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
from pathlib import Path
from typing import Any

import httpx
from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field, create_model

from interaktiv.kyra.agent.core.schemas import (
    BLOCK_TYPES,
    SKIP_TOOL_TYPES,
    ContentNode,
    MetadataUpdate,
    Result,
    Site,
)

# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _fmt(result: Result) -> str:
    return _json(result.model_dump())


def _node_to_dict(node: ContentNode) -> dict[str, Any]:
    d: dict[str, Any] = {
        "path": node.path,
        "title": node.title,
        "content_type": node.content_type,
    }
    if node.description:
        d["description"] = node.description
    if node.subjects:
        d["subjects"] = node.subjects
    if node.preview_image:
        d["preview_image"] = node.preview_image
    if node.has_children:
        d["has_children"] = True
    if node.published:
        d["published"] = node.published.isoformat()
    return d


# ---------------------------------------------------------------------------
# Shared field types for layout tool schemas
# ---------------------------------------------------------------------------

PageField = (
    str | None,
    Field(
        default=None,
        description="Page path, e.g. '/about/team'. Defaults to the current page.",
    ),
)
ContainerPath = (
    str,
    Field(
        description="Block container path within the page. '/' for page root. Example: '/columns_1/column_1'."
    ),
)
ElementName = (
    str,
    Field(description="Element name in snake_case, unique within its container."),
)
AfterRef = (
    str | None,
    Field(default=None, description="Insert after this sibling name."),
)
BeforeRef = (
    str | None,
    Field(default=None, description="Insert before this sibling name."),
)
ToStartFlag = (
    bool,
    Field(default=False, description="Insert at the beginning of the container."),
)


# ---------------------------------------------------------------------------
# Per-type tool factories
# ---------------------------------------------------------------------------


def _make_create_tool(
    site: Site, type_name: str, attrs_model: type[BaseModel]
) -> BaseTool:
    schema = create_model(
        f"Create{type_name.title().replace('_', '')}Input",
        page=PageField,
        path=ContainerPath,
        name=ElementName,
        attributes=(attrs_model, ...),
        after=AfterRef,
        before=BeforeRef,
        to_start=ToStartFlag,
    )

    async def run(**kwargs: Any) -> str:
        attrs = kwargs["attributes"]
        return _fmt(
            await site.create_element(
                kwargs.get("page") or site.current_page,
                block_type=type_name,
                path=kwargs["path"],
                name=kwargs["name"],
                attributes=(
                    attrs.model_dump() if hasattr(attrs, "model_dump") else attrs
                ),
                after=kwargs.get("after"),
                before=kwargs.get("before"),
                to_start=kwargs.get("to_start", False),
            )
        )

    return tool(
        f"create_{type_name}",
        description=f"Create a new `{type_name}` element.",
        args_schema=schema,
    )(run)


def _make_update_tool(
    site: Site, type_name: str, update_model: type[BaseModel]
) -> BaseTool:
    schema = create_model(
        f"Update{type_name.title().replace('_', '')}Input",
        page=PageField,
        path=ContainerPath,
        name=ElementName,
        attributes=(update_model, ...),
    )

    async def run(**kwargs: Any) -> str:
        attrs = kwargs["attributes"]
        return _fmt(
            await site.update_element(
                kwargs.get("page") or site.current_page,
                path=kwargs["path"],
                name=kwargs["name"],
                attributes=(
                    attrs.model_dump(exclude_none=True)
                    if hasattr(attrs, "model_dump")
                    else attrs
                ),
            )
        )

    return tool(
        f"update_{type_name}",
        description=f"Update an existing `{type_name}`. Only provided fields change.",
        args_schema=schema,
    )(run)


# ---------------------------------------------------------------------------
# Input schemas for site browsing tools
# ---------------------------------------------------------------------------


class ListChildrenInput(BaseModel):
    path: str | None = Field(
        default=None,
        description="Content path to list children of. Defaults to the current page's parent.",
    )
    content_type: str | None = Field(
        default=None, description="Filter children by content type."
    )
    limit: int = Field(default=10, ge=1, le=25, description="Max children to return.")
    offset: int = Field(default=0, ge=0, description="Skip this many children.")


class SearchContentInput(BaseModel):
    query: str | None = Field(default=None, description="Full-text search terms.")
    path: str | None = Field(default=None, description="Restrict to a subtree.")
    content_type: str | None = Field(
        default=None, description="Filter by content type."
    )
    subjects: list[str] | None = Field(
        default=None, description="Filter by tags (all must match)."
    )
    limit: int = Field(default=10, ge=1, le=25, description="Max results.")


class GetBreadcrumbInput(BaseModel):
    path: str | None = Field(
        default=None, description="Content path. Defaults to current page."
    )


# ---------------------------------------------------------------------------
# Input schemas for layout tools
# ---------------------------------------------------------------------------


class GetLayoutInput(BaseModel):
    page: str | None = Field(
        default=None, description="Page path. Defaults to the current page."
    )
    path: str | None = Field(
        default=None, description="Block container path. Null for page root."
    )
    name: str | None = Field(
        default=None, description="Element name. Null for all elements."
    )


class GetMetadataInput(BaseModel):
    page: str | None = Field(
        default=None, description="Page path. Defaults to the current page."
    )


class DeleteElementInput(BaseModel):
    page: str | None = Field(
        default=None, description="Page path. Defaults to the current page."
    )
    path: str = Field(description="Block container path.")
    name: str = Field(description="Element name to delete.")


class SwapElementsInput(BaseModel):
    page: str | None = Field(
        default=None, description="Page path. Defaults to the current page."
    )
    path_a: str = Field(description="Container path of the first element.")
    name_a: str = Field(description="First element name.")
    path_b: str = Field(description="Container path of the second element.")
    name_b: str = Field(description="Second element name.")


class MoveElementInput(BaseModel):
    page: str | None = Field(
        default=None, description="Page path. Defaults to the current page."
    )
    path: str = Field(description="Current block container path.")
    name: str = Field(description="Element name to move.")
    to_path: str = Field(description="Destination block container path.")
    after_name: str | None = Field(
        default=None, description="Insert after this sibling."
    )
    before_name: str | None = Field(
        default=None, description="Insert before this sibling."
    )
    to_start: bool = Field(default=False, description="Insert at beginning.")
    new_name: str | None = Field(default=None, description="Rename in destination.")


class CopyElementInput(BaseModel):
    page: str | None = Field(
        default=None, description="Destination page path. Defaults to the current page."
    )
    source_page: str | None = Field(
        default=None,
        description="Source page to copy from. Defaults to the destination page. "
        "Use this to copy elements from another page to the current page.",
    )
    path: str = Field(description="Block container path of the source element.")
    name: str = Field(description="Element name to copy.")
    to_path: str = Field(description="Destination block container path.")
    after_name: str | None = Field(
        default=None, description="Insert after this sibling."
    )
    before_name: str | None = Field(
        default=None, description="Insert before this sibling."
    )
    to_start: bool = Field(default=False, description="Insert at beginning.")
    new_name: str | None = Field(default=None, description="Name for the copy.")


class UpdateMetadataInput(BaseModel):
    page: str | None = Field(
        default=None, description="Page path. Defaults to the current page."
    )
    attributes: MetadataUpdate = Field(description="Metadata fields to update.")


class SearchDocumentsInput(BaseModel):
    query: str = Field(description="Search terms to find in documents.")
    path: str | None = Field(
        default=None, description="Restrict to documents under this subtree."
    )
    limit: int = Field(default=5, ge=1, le=20, description="Max chunks to return.")


class ReadDocumentPagesInput(BaseModel):
    path: str = Field(description="Content path of the document (e.g. a .pdf file).")
    start_page: int = Field(
        default=1, ge=1, description="First page to read (1-indexed)."
    )
    end_page: int = Field(default=5, ge=1, description="Last page to read (inclusive).")


class ViewImageInput(BaseModel):
    path: str = Field(
        description="Content path of an Image or any content with a preview image."
    )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_tools(site: Site) -> list[BaseTool]:
    """Build all agent tools: site browsing + layout read/write."""

    # --- Site browsing ---

    @tool(args_schema=ListChildrenInput)
    async def list_children(
        path: str | None = None,
        content_type: str | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> str:
        """List the direct children of a content node."""
        if path is None:
            path = "/".join(site.current_page.rstrip("/").split("/")[:-1]) or "/"
        node = await site.get_node(path)
        if node is None:
            return _json({"error": f"Path '{path}' not found."})
        children = await site.get_children(
            path, content_type=content_type, limit=limit, offset=offset
        )
        return _json(
            {"children": [_node_to_dict(c) for c in children], "count": len(children)}
        )

    @tool(args_schema=SearchContentInput)
    async def search_content(
        query: str | None = None,
        path: str | None = None,
        content_type: str | None = None,
        subjects: list[str] | None = None,
        limit: int = 10,
    ) -> str:
        """Search for content across the site."""
        if not any([query, path, content_type, subjects]):
            return _json(
                {
                    "error": "Provide at least one of: query, path, content_type, subjects."
                }
            )
        results = await site.search(
            query=query,
            path=path,
            content_type=content_type,
            subjects=subjects,
            limit=limit,
        )
        return _json(
            {"results": [_node_to_dict(r) for r in results], "count": len(results)}
        )

    @tool(args_schema=GetBreadcrumbInput)
    async def get_breadcrumb(path: str | None = None) -> str:
        """Get the ancestor chain for a content path."""
        target = path or site.current_page
        ancestors, node = await asyncio.gather(
            site.get_ancestors(target),
            site.get_node(target),
        )
        result: dict[str, Any] = {"ancestors": [_node_to_dict(a) for a in ancestors]}
        if node is not None:
            result["node"] = _node_to_dict(node)
        return _json(result)

    # --- Documents ---

    @tool(args_schema=SearchDocumentsInput)
    async def search_documents(
        query: str, path: str | None = None, limit: int = 5
    ) -> str:
        """Search within documents (PDFs, files) stored on the site. Returns
        text chunks from documents that match the query, with source path and
        page number. Use this to find specific information buried in PDFs or
        long documents — e.g. regulations, forms, reports."""
        chunks = await site.search_documents(query, path=path, limit=limit)
        if not chunks:
            return _json(
                {"results": [], "message": "No matching document content found."}
            )
        return _json(
            {
                "results": [
                    {
                        "source": c.source_path,
                        "title": c.source_title,
                        "text": c.text,
                        **({"page": c.page} if c.page is not None else {}),
                    }
                    for c in chunks
                ],
                "count": len(chunks),
            }
        )

    @tool(args_schema=ReadDocumentPagesInput)
    async def read_document_pages(
        path: str, start_page: int = 1, end_page: int = 5
    ) -> str:
        """Read specific pages from a document (PDF). Returns the full markdown
        content of the requested page range. Use this after search_documents to
        read surrounding context, or to browse a document section by section.
        Maximum 5 pages per request."""
        if end_page - start_page >= 5:
            end_page = start_page + 4
        return _fmt(
            await site.read_document_pages(
                path, start_page=start_page, end_page=end_page
            )
        )

    # --- Images ---

    @tool(args_schema=ViewImageInput)
    async def view_image(path: str) -> list[dict[str, Any]]:
        """View an image from the site. Shows you the actual image so you can
        judge its content, write alt text, or decide how to use it in a layout.
        Use the content path (e.g. '/bilder/campus-herbst.jpg') as image_url
        or preview_image in blocks — the CMS resolves it automatically."""
        url, node = await asyncio.gather(
            site.resolve_image(path),
            site.get_node(path),
        )
        if url is None:
            return [{"type": "text", "text": f"No image found at '{path}'."}]
        caption = f"{node.title} ({path})" if node else path

        # Local file path — read and return as a data-URL image block.
        local = Path(url)
        if local.is_file():
            size = local.stat().st_size
            if size > 5 * 1024 * 1024:
                return [
                    {
                        "type": "text",
                        "text": f"{caption}\n(Image too large: {size:,} bytes.)",
                    }
                ]
            data = local.read_bytes()
            b64 = base64.standard_b64encode(data).decode()
            mime = mimetypes.guess_type(local.name)[0] or "image/jpeg"
            return [
                {"type": "text", "text": caption},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                },
            ]

        # HTTP URL — verify reachable, then return.
        if url.startswith("http"):
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.head(url, follow_redirects=True, timeout=5)
                    if resp.status_code >= 400:
                        return [
                            {
                                "type": "text",
                                "text": f"{caption}\n(Image URL returned {resp.status_code}, cannot display.)",
                            }
                        ]
            except httpx.HTTPError:
                return [
                    {
                        "type": "text",
                        "text": f"{caption}\n(Image URL unreachable, cannot display.)",
                    }
                ]
        return [
            {"type": "text", "text": caption},
            {"type": "image", "url": url},
        ]

    # --- Layout read ---

    @tool(args_schema=GetLayoutInput)
    async def get_layout(
        page: str | None = None, path: str | None = None, name: str | None = None
    ) -> str:
        """Read a page's block layout."""
        return _fmt(
            await site.get_layout(
                page or site.current_page, path=path or "/", name=name
            )
        )

    @tool(args_schema=GetMetadataInput)
    async def get_metadata(page: str | None = None) -> str:
        """Read a page's metadata."""
        return _fmt(await site.get_metadata(page or site.current_page))

    tools: list[BaseTool] = [
        list_children,
        search_content,
        get_breadcrumb,
        search_documents,
        read_document_pages,
        view_image,
        get_layout,
        get_metadata,
    ]

    # --- Per-type create & update ---

    for type_name, (attrs_model, update_model) in BLOCK_TYPES.items():
        if type_name in SKIP_TOOL_TYPES:
            continue
        tools.append(_make_create_tool(site, type_name, attrs_model))
        tools.append(_make_update_tool(site, type_name, update_model))

    # --- Generic mutation tools ---

    @tool(args_schema=DeleteElementInput)
    async def delete_element(path: str, name: str, page: str | None = None) -> str:
        """Delete an element from a page."""
        return _fmt(
            await site.delete_element(page or site.current_page, path=path, name=name)
        )

    @tool(args_schema=SwapElementsInput)
    async def swap_elements(
        path_a: str,
        name_a: str,
        path_b: str,
        name_b: str,
        page: str | None = None,
    ) -> str:
        """Swap the positions of two elements across any containers."""
        return _fmt(
            await site.swap_elements(
                page or site.current_page,
                path_a=path_a,
                name_a=name_a,
                path_b=path_b,
                name_b=name_b,
            )
        )

    @tool(args_schema=MoveElementInput)
    async def move_element(
        path: str,
        name: str,
        to_path: str,
        after_name: str | None = None,
        before_name: str | None = None,
        to_start: bool = False,
        new_name: str | None = None,
        page: str | None = None,
    ) -> str:
        """Move or reorder an element within a page."""
        return _fmt(
            await site.move_element(
                page or site.current_page,
                path=path,
                name=name,
                to_path=to_path,
                after_name=after_name,
                before_name=before_name,
                to_start=to_start,
                new_name=new_name,
            )
        )

    @tool(args_schema=CopyElementInput)
    async def copy_element(
        path: str,
        name: str,
        to_path: str,
        source_page: str | None = None,
        after_name: str | None = None,
        before_name: str | None = None,
        to_start: bool = False,
        new_name: str | None = None,
        page: str | None = None,
    ) -> str:
        """Copy an element and its children. Use source_page to copy from
        another page to the current page."""
        return _fmt(
            await site.copy_element(
                page or site.current_page,
                source_page=source_page,
                path=path,
                name=name,
                to_path=to_path,
                after_name=after_name,
                before_name=before_name,
                to_start=to_start,
                new_name=new_name,
            )
        )

    @tool(args_schema=UpdateMetadataInput)
    async def update_metadata(
        attributes: MetadataUpdate, page: str | None = None
    ) -> str:
        """Update page metadata. Only provided fields change."""
        return _fmt(
            await site.update_metadata(
                page or site.current_page,
                attributes=attributes.model_dump(exclude_none=True),
            )
        )

    tools.extend(
        [delete_element, swap_elements, move_element, copy_element, update_metadata]
    )
    return tools
