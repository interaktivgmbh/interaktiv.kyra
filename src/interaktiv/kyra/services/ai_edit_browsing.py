"""Plone Site browser for the Layout Agent.

All Plone catalog access happens at **conversation creation time** (inside the
Plone request thread).  The resulting ``SiteSnapshot`` is stored on the
``Conversation`` object.  The LangChain tools query the snapshot — they never
call ``plone.api`` themselves, so they are safe to run on the agent's daemon
thread.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from interaktiv.kyra.agent.core.schemas import ContentNode

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Plone → ContentNode converters  (called only at snapshot time)
# ---------------------------------------------------------------------------


def _to_datetime(val: Any) -> datetime | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    if hasattr(val, "ISO8601"):
        try:
            return datetime.fromisoformat(val.ISO8601())
        except Exception:
            pass
    if hasattr(val, "asdatetime"):
        try:
            return val.asdatetime()
        except Exception:
            pass
    return None


def _brain_to_node(brain: Any, portal_len: int) -> ContentNode:
    """Convert a catalog brain to a ContentNode."""
    parts = brain.getPath().split("/")
    rel_path = "/" + "/".join(parts[portal_len:]) or "/"

    subjects: list[str] = []
    raw_subjects = getattr(brain, "Subject", None)
    if raw_subjects:
        subjects = list(raw_subjects) if not isinstance(raw_subjects, str) else [raw_subjects]

    return ContentNode(
        path=rel_path,
        title=brain.Title or rel_path.rsplit("/", 1)[-1],
        description=brain.Description or "",
        content_type=brain.portal_type or "",
        has_children=getattr(brain, "is_folderish", False),
        subjects=subjects,
        preview_image=getattr(brain, "image_field", "") or "",
        created=_to_datetime(getattr(brain, "created", None)),
        modified=_to_datetime(getattr(brain, "modified", None)),
        published=_to_datetime(getattr(brain, "effective", None)),
    )


def _obj_to_node(obj: Any, portal_len: int) -> ContentNode:
    """Convert a Plone content object to a ContentNode."""
    parts = obj.getPhysicalPath()
    path = "/" + "/".join(parts[portal_len:]) or "/"

    title = ""
    try:
        title = obj.Title() if callable(getattr(obj, "Title", None)) else getattr(obj, "title", "")
    except Exception:
        title = getattr(obj, "id", path.rsplit("/", 1)[-1])

    description = ""
    try:
        description = obj.Description() if callable(getattr(obj, "Description", None)) else ""
    except Exception:
        pass

    has_children = getattr(obj, "is_folderish", False)
    if not has_children:
        has_children = hasattr(obj, "objectIds") and bool(obj.objectIds())

    subjects: list[str] = []
    try:
        raw = obj.Subject() if callable(getattr(obj, "Subject", None)) else []
        subjects = list(raw) if raw else []
    except Exception:
        pass

    return ContentNode(
        path=path,
        title=title or path.rsplit("/", 1)[-1],
        description=description,
        content_type=getattr(obj, "portal_type", ""),
        has_children=has_children,
        subjects=subjects,
    )


# ---------------------------------------------------------------------------
# SiteSnapshot — pre-loaded, thread-safe site tree
# ---------------------------------------------------------------------------


@dataclass
class SiteSnapshot:
    """In-memory snapshot of the Plone site tree, loaded once at conversation
    creation time.  All fields are plain Python data — no Plone objects.
    """

    # All nodes indexed by path for fast look-up
    nodes_by_path: dict[str, ContentNode] = field(default_factory=dict)
    # parent_path → [child nodes] sorted by position
    children_by_parent: dict[str, list[ContentNode]] = field(default_factory=dict)
    # All nodes (flat) for search
    all_nodes: list[ContentNode] = field(default_factory=list)
    # Image metadata by path
    images: dict[str, dict[str, Any]] = field(default_factory=dict)


def load_site_snapshot() -> SiteSnapshot:
    """Load the entire site tree from the Plone catalog.

    MUST be called from the Plone request thread (during conversation
    creation).
    """
    from plone import api

    snap = SiteSnapshot()

    try:
        portal = api.portal.get()
        catalog = api.portal.get_tool("portal_catalog")
        portal_parts = portal.getPhysicalPath()
        portal_path = "/".join(portal_parts)
        portal_len = len(portal_parts)

        brains = catalog.searchResults(
            path={"query": portal_path},
            sort_on="getObjPositionInParent",
        )

        for brain in brains:
            try:
                node = _brain_to_node(brain, portal_len)
                if not node.path or node.path == "/":
                    continue
                snap.nodes_by_path[node.path] = node
                snap.all_nodes.append(node)

                # Index by parent
                parent = "/".join(node.path.rstrip("/").split("/")[:-1]) or "/"
                snap.children_by_parent.setdefault(parent, []).append(node)

                # Index images
                if node.content_type == "Image":
                    snap.images[node.path] = {
                        "path": node.path,
                        "title": node.title,
                        "description": node.description,
                    }
            except Exception:
                logger.debug("[browse] Failed to index %s", brain.getPath(), exc_info=True)

        logger.info(
            "[browse] Site snapshot loaded: %d nodes, %d images",
            len(snap.all_nodes),
            len(snap.images),
        )
    except Exception:
        logger.warning("[browse] Site snapshot loading failed", exc_info=True)

    return snap


# ---------------------------------------------------------------------------
# Query functions (operate on snapshot, safe for daemon thread)
# ---------------------------------------------------------------------------


def snap_get_children(
    snap: SiteSnapshot,
    path: str,
    *,
    content_type: str | None = None,
    limit: int = 25,
) -> list[ContentNode]:
    children = snap.children_by_parent.get(path.rstrip("/") or "/", [])
    if content_type:
        children = [c for c in children if c.content_type == content_type]
    return children[:limit]


def snap_get_ancestors(snap: SiteSnapshot, path: str) -> list[ContentNode]:
    segments = [s for s in path.strip("/").split("/") if s]
    ancestors: list[ContentNode] = []
    for i in range(len(segments) - 1):
        ancestor_path = "/" + "/".join(segments[: i + 1])
        node = snap.nodes_by_path.get(ancestor_path)
        if node:
            ancestors.append(node)
    return ancestors


def snap_get_breadcrumb(snap: SiteSnapshot, path: str) -> list[ContentNode]:
    segments = [s for s in path.strip("/").split("/") if s]
    nodes: list[ContentNode] = []
    for i in range(len(segments)):
        p = "/" + "/".join(segments[: i + 1])
        node = snap.nodes_by_path.get(p)
        if node:
            nodes.append(node)
    return nodes


def snap_search(
    snap: SiteSnapshot,
    *,
    query: str | None = None,
    path: str | None = None,
    content_type: str | None = None,
    subjects: list[str] | None = None,
    limit: int = 10,
) -> list[ContentNode]:
    results = snap.all_nodes
    if path:
        prefix = path.rstrip("/") + "/"
        results = [n for n in results if n.path.startswith(prefix) or n.path == path.rstrip("/")]
    if content_type:
        results = [n for n in results if n.content_type == content_type]
    if subjects:
        subject_set = set(subjects)
        results = [n for n in results if subject_set & set(n.subjects)]
    if query:
        terms = query.lower().split()
        scored: list[tuple[int, ContentNode]] = []
        for n in results:
            text = f"{n.title} {n.description} {' '.join(n.subjects)}".lower()
            hits = sum(1 for t in terms if t in text)
            if hits > 0:
                scored.append((hits, n))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [n for _, n in scored[:limit]]
    return results[:limit]


# ---------------------------------------------------------------------------
# build_page_context — uses snapshot
# ---------------------------------------------------------------------------


def build_page_context(snap: SiteSnapshot, page_path: str) -> str:
    """Build context string with ancestors, siblings, and children."""
    parts: list[str] = []

    ancestors = snap_get_ancestors(snap, page_path)
    if ancestors:
        parts.append("Elternhierarchie:")
        for node in ancestors:
            parts.append(_format_node(node))

    parent_path = "/".join(page_path.rstrip("/").split("/")[:-1]) or "/"
    if parent_path != page_path:
        siblings = snap_get_children(snap, parent_path, limit=25)
        others = [s for s in siblings if s.path.rstrip("/") != page_path.rstrip("/")]
        if others:
            parts.append("Geschwisterseiten:")
            for node in others:
                parts.append(_format_node(node))

    children = snap_get_children(snap, page_path, limit=25)
    if children:
        parts.append("Unterseiten:")
        for node in children:
            parts.append(_format_node(node))

    return "\n".join(parts)


def _format_node(node: ContentNode) -> str:
    line = f"  {node.path} — {node.title} ({node.content_type})"
    if node.description:
        line += f" — {node.description}"
    return line


# ---------------------------------------------------------------------------
# LangChain browsing tools (operate on snapshot)
# ---------------------------------------------------------------------------


class ListChildrenInput(BaseModel):
    path: str = Field(description="Content path to list children of, e.g. '/' or '/leben/freizeit'.")
    content_type: str | None = Field(default=None, description="Filter by content type, e.g. 'Document', 'News Item'.")
    limit: int = Field(default=25, ge=1, le=50, description="Max results.")


class SearchContentInput(BaseModel):
    query: str | None = Field(default=None, description="Full-text search query.")
    path: str | None = Field(default=None, description="Restrict to subtree, e.g. '/leben'.")
    content_type: str | None = Field(default=None, description="Filter by type, e.g. 'Document', 'Event'.")
    subjects: list[str] | None = Field(default=None, description="Filter by subject tags.")
    limit: int = Field(default=10, ge=1, le=25, description="Max results.")


class GetBreadcrumbInput(BaseModel):
    path: str = Field(description="Content path to get the breadcrumb for.")


class ViewImageInput(BaseModel):
    path: str = Field(description="Content path of the image, e.g. '/bilder/hero'.")


def _nodes_to_json(nodes: list[ContentNode]) -> str:
    return json.dumps(
        [n.model_dump(exclude_none=True, exclude_defaults=True) for n in nodes],
        ensure_ascii=False,
        default=str,
    )


def make_browsing_tools(snap: SiteSnapshot) -> list:
    """Create browsing tools bound to a pre-loaded SiteSnapshot.

    These tools are safe to run on the agent's daemon thread because they
    only read from the in-memory snapshot — no ``plone.api`` calls.
    """

    @tool(args_schema=ListChildrenInput)
    def list_children(path: str, content_type: str | None = None, limit: int = 25) -> str:
        """List direct children of a content path. Shows title, type, description, and whether it has sub-pages."""
        nodes = snap_get_children(snap, path, content_type=content_type, limit=limit)
        if not nodes:
            return f"No children found at '{path}'."
        return _nodes_to_json(nodes)

    @tool(args_schema=SearchContentInput)
    def search_content(
        query: str | None = None,
        path: str | None = None,
        content_type: str | None = None,
        subjects: list[str] | None = None,
        limit: int = 10,
    ) -> str:
        """Search the website for content by text, path, type, or subject tags."""
        nodes = snap_search(
            snap,
            query=query,
            path=path,
            content_type=content_type,
            subjects=subjects,
            limit=limit,
        )
        if not nodes:
            return "No results found."
        return _nodes_to_json(nodes)

    @tool(args_schema=GetBreadcrumbInput)
    def get_breadcrumb(path: str) -> str:
        """Show the parent hierarchy (breadcrumb) from the root to a content path."""
        nodes = snap_get_breadcrumb(snap, path)
        if not nodes:
            return f"No breadcrumb found for '{path}'."
        return _nodes_to_json(nodes)

    @tool(args_schema=ViewImageInput)
    def view_image(path: str) -> str:
        """View an image from the website to judge its content and write alt-text."""
        info = snap.images.get(path)
        if info is None:
            return json.dumps({"ok": False, "message": f"Image not found at '{path}'."})
        return json.dumps({"ok": True, **info}, ensure_ascii=False)

    return [list_children, search_content, get_breadcrumb, view_image]
