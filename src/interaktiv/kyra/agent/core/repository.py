"""In-memory Site implementation for testing and development."""

from __future__ import annotations

import copy
import json
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar, cast

from pydantic import BaseModel, ValidationError

from interaktiv.kyra.agent.core.schemas import (
    BLOCK_MODELS,
    BLOCK_TYPES,
    DocumentChunk,
    ContentNode,
    DescriptionAttributes,
    Layout,
    ListingAttributes,
    ListingItemAttributes,
    ListingItemBlock,
    ListingQuery,
    Metadata,
    MetadataUpdate,
    PageState,
    ContentTypeFilter,
    PathFilter,
    Result,
    Site,
    SubjectFilter,
    TitleAttributes,
)

ModelT = TypeVar("ModelT", bound=BaseModel)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_under(child: str, parent: str) -> bool:
    """Check if child path is strictly under parent path."""
    return child.startswith(parent.rstrip("/") + "/")


def _parent_path(path: str) -> str | None:
    if path == "/":
        return None
    parts = path.rstrip("/").rsplit("/", 1)
    return parts[0] if parts[0] else "/"


def _err(message: str) -> Result:
    return Result(ok=False, message=message)


def _ok(message: str, data: Any = None) -> Result:
    return Result(ok=True, message=message, data=data)


def _validate(
    model_cls: type[ModelT], data: dict[str, Any], prefix: str
) -> ModelT | Result:
    try:
        return model_cls(**data)
    except ValidationError as exc:
        msgs = "; ".join(e["msg"] for e in exc.errors())
        return _err(f"{prefix}: {msgs}.")


BlockContainer = list[Any]

CHILD_TYPES: dict[str, str] = {
    "slider": "slide",
    "carousel": "carousel_item",
    "columns": "column",
    "accordion": "accordion_panel",
    "listing": "listing_item",
    "statistic": "statistic_item",
    "tabs": "tab",
}

FORM_FIELD_TYPES = {
    "form_field",
    "form_choice",
}

# Containers that accept multiple specific child types.
RESTRICTED_CHILD_TYPES: dict[str, set[str]] = {
    "form": FORM_FIELD_TYPES | {"rich_text"},
}

CHILD_ONLY_TYPES = set(CHILD_TYPES.values()) | FORM_FIELD_TYPES
OPEN_CONTAINER_TYPES = {"column", "accordion_panel", "tab"}
CONTAINER_TYPES = (
    set(CHILD_TYPES.keys()) | OPEN_CONTAINER_TYPES | set(RESTRICTED_CHILD_TYPES.keys())
)
PARENT_NEEDED: dict[str, str] = {v: k for k, v in CHILD_TYPES.items()}
for _ft in FORM_FIELD_TYPES:
    PARENT_NEEDED[_ft] = "form"


def _names(container: BlockContainer) -> list[str]:
    return [b.name for b in container]


def _find(container: BlockContainer, name: str) -> tuple[int, Any] | None:
    for i, block in enumerate(container):
        if block.name == name:
            return i, block
    return None


def _resolve_position(
    container: BlockContainer, after: str | None, before: str | None, to_start: bool
) -> int | Result:
    specs: list[tuple[str, int]] = []
    if to_start:
        specs.append(("to_start", 0))
    if after is not None:
        found = _find(container, after)
        if found is None:
            return _err(
                f"Position ref '{after}' not found. Available: {', '.join(_names(container))}."
            )
        specs.append(("after", found[0] + 1))
    if before is not None:
        found = _find(container, before)
        if found is None:
            return _err(
                f"Position ref '{before}' not found. Available: {', '.join(_names(container))}."
            )
        specs.append(("before", found[0]))
    if not specs:
        return len(container)
    indices = {idx for _, idx in specs}
    if len(indices) > 1:
        return _err(
            f"Conflicting positions: {', '.join(f'{l}={i}' for l, i in specs)}."
        )
    return specs[0][1]


def _update_paths(block: Any, new_path: str) -> None:
    block.path = new_path
    if hasattr(block, "children"):
        child_path = new_path.rstrip("/") + "/" + block.name
        for child in block.children:
            _update_paths(child, child_path)


def _deep_copy(block: Any) -> Any:
    clone = block.model_copy(deep=True)
    _assign_fresh_ids(clone)
    return clone


def _assign_fresh_ids(block: Any) -> None:
    block.id = str(uuid.uuid4())
    if hasattr(block, "children"):
        for child in block.children:
            _assign_fresh_ids(child)


# ---------------------------------------------------------------------------
# Document chunking
# ---------------------------------------------------------------------------


def _get_splitter() -> Any:
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    return RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)


_splitter: Any = None


def _chunk_text(text: str, node: ContentNode) -> list[DocumentChunk]:
    """Split inline text into chunks using RecursiveCharacterTextSplitter."""
    global _splitter
    if _splitter is None:
        _splitter = _get_splitter()
    docs = _splitter.create_documents([text])
    return [
        DocumentChunk(
            source_path=node.path,
            source_title=node.title,
            text=doc.page_content,
            page=i + 1 if len(docs) > 1 else None,
        )
        for i, doc in enumerate(docs)
    ]


def _load_file(
    file_path: Path, node: ContentNode
) -> tuple[list[DocumentChunk], list[str] | None]:
    """Extract text from a file. Returns (chunks, per_page_texts).

    per_page_texts is a list of markdown strings, one per page (0-indexed).
    Only populated for PDFs; None for other file types.
    """
    if not file_path.exists():
        return [], None

    suffix = file_path.suffix.lower()
    if suffix == ".pdf":
        import pymupdf4llm

        page_dicts = pymupdf4llm.to_markdown(str(file_path), page_chunks=True)
        if not isinstance(page_dicts, list):
            return _chunk_text(str(page_dicts), node), None
        pages = [str(d["text"]) for d in page_dicts]
        full_text = "\n\n".join(pages)
        return _chunk_text(full_text, node), pages

    text = file_path.read_text(errors="replace")
    return _chunk_text(text, node), None


# ---------------------------------------------------------------------------
# Page shadow
# ---------------------------------------------------------------------------


class _PageShadow:
    def __init__(self, metadata: Metadata, layout: Layout) -> None:
        self.metadata = metadata
        self.layout = layout


# ---------------------------------------------------------------------------
# InMemorySite
# ---------------------------------------------------------------------------


class InMemorySite(Site):
    """Full Site implementation backed by in-memory data.

    Holds a content tree (flat list of ContentNode) and page shadows
    for layout operations. Useful for testing and development.
    """

    def __init__(self, nodes: list[ContentNode]) -> None:
        self._nodes: dict[str, ContentNode] = {}
        self._children: dict[str, list[str]] = defaultdict(list)
        self._pages: dict[str, _PageShadow] = {}
        self._chunks: list[DocumentChunk] = []
        self._document_pages: dict[str, list[str]] = {}
        self._current_page: str = ""
        self._listings_dirty: bool = True

        for node in nodes:
            self._nodes[node.path] = node

        for path in self._nodes:
            parent = _parent_path(path)
            if parent is not None and parent in self._nodes:
                self._children[parent].append(path)

        for parent_path in self._children:
            self._nodes[parent_path].has_children = True

    @property
    def current_page(self) -> str:
        return self._current_page

    def set_current_page(self, page: str) -> None:
        self._current_page = page

    def add_node(self, node: ContentNode) -> None:
        """Register a content node in the tree (updates parent/children)."""
        self._nodes[node.path] = node
        parent = _parent_path(node.path)
        if parent is not None and parent in self._nodes:
            if node.path not in self._children[parent]:
                self._children[parent].append(node.path)
            self._nodes[parent].has_children = True
        self._listings_dirty = True

    def _check_current_page(self, page: str) -> Result | None:
        """Return an error if page is not the current page. No-op when unset."""
        if self._current_page and page != self._current_page:
            return _err(
                f"Cannot modify '{page}': you are currently on '{self._current_page}'. "
                f"You can only modify the current page."
            )
        return None

    @classmethod
    def from_json(
        cls, data: list[dict[str, Any]], *, base_dir: Path | None = None
    ) -> InMemorySite:
        """Load from a flat list of node dicts.

        Each entry must have path, title, content_type (standard ContentNode fields).
        Optional extras:
        - "layout": list of block dicts → pre-built page content
        - "text": string → document text, auto-chunked
        - "file": string → path to a file on disk (e.g. PDF), extracted and chunked
        """
        nodes: list[ContentNode] = []
        pages: dict[str, PageState] = {}
        chunks: list[DocumentChunk] = []
        doc_pages: dict[str, list[str]] = {}

        _extra_keys = {"layout", "text", "file"}

        for entry in data:
            layout_data = entry.get("layout")
            text_data = entry.get("text")
            file_path = entry.get("file")

            node = ContentNode.model_validate(
                {k: v for k, v in entry.items() if k not in _extra_keys}
            )
            nodes.append(node)

            # Pre-built page layout
            if layout_data is not None:
                state = PageState(
                    metadata=Metadata(
                        path=node.path,
                        title=node.title,
                        description=node.description,
                        preview_image=node.preview_image,
                        subjects=list(node.subjects),
                        start=node.start,
                        end=node.end,
                    ),
                    layout=Layout.model_validate(layout_data),
                )
                pages[node.path] = state

            # File on disk → extract text and chunk
            if file_path is not None:
                resolved = Path(file_path)
                if base_dir is not None and not resolved.is_absolute():
                    resolved = base_dir / resolved
                file_chunks, per_page = _load_file(resolved, node)
                chunks.extend(file_chunks)
                if per_page is not None:
                    doc_pages[node.path] = per_page
            # Inline text → chunk
            elif text_data is not None:
                chunks.extend(_chunk_text(text_data, node))

        site = cls(nodes)
        for page_path, state in pages.items():
            site.add_page(page_path, state)
        site._chunks = chunks
        site._document_pages = doc_pages

        # Resolve listing blocks in pre-built pages.
        site._listings_dirty = True
        site._refresh_all_listings()

        return site

    @classmethod
    def from_file(cls, path: str | Path) -> InMemorySite:
        p = Path(path)
        raw = json.loads(p.read_text())
        return cls.from_json(raw, base_dir=p.parent)

    def add_page(self, page_path: str, state: PageState | None = None) -> None:
        """Register a page for layout operations. Creates empty state if not provided.
        No-op if the page already exists."""
        if page_path in self._pages:
            return
        if state is None:
            state = PageState(
                metadata=Metadata(path=page_path), layout=Layout.model_validate([])
            )
        self._pages[page_path] = _PageShadow(
            copy.deepcopy(state.metadata),
            copy.deepcopy(state.layout),
        )

    def _get_page(self, page: str) -> _PageShadow | Result:
        shadow = self._pages.get(page)
        if shadow is not None:
            return shadow
        # Auto-create empty page for known content nodes.
        node = self._nodes.get(page)
        if node is not None:
            self.add_page(page)
            return self._pages[page]
        return _err(f"Page '{page}' not found.")

    # --- Content tree ---

    async def get_node(self, path: str) -> ContentNode | None:
        return self._nodes.get(path)

    async def get_ancestors(self, path: str) -> list[ContentNode]:
        ancestors: list[ContentNode] = []
        current = _parent_path(path)
        while current is not None:
            node = self._nodes.get(current)
            if node is not None:
                ancestors.append(node)
            current = _parent_path(current)
        ancestors.reverse()
        return ancestors

    async def get_children(
        self,
        path: str,
        *,
        content_type: str | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> list[ContentNode]:
        child_paths = self._children.get(path, [])
        children = [self._nodes[p] for p in child_paths]
        if content_type is not None:
            children = [c for c in children if c.content_type == content_type]
        return children[offset : offset + limit]

    def _search_sync(
        self,
        *,
        query: str | None = None,
        path: str | None = None,
        content_type: str | None = None,
        subjects: list[str] | None = None,
        limit: int = 10,
    ) -> list[ContentNode]:
        results: list[ContentNode] = []
        for node in self._nodes.values():
            if path is not None:
                if not _is_under(node.path, path):
                    continue
            if content_type is not None and node.content_type != content_type:
                continue
            if subjects is not None and not all(s in node.subjects for s in subjects):
                continue
            if query is not None:
                haystack = f"{node.title} {node.description}".lower()
                if not all(term in haystack for term in query.lower().split()):
                    continue
            results.append(node)
            if len(results) >= limit:
                break
        return results

    async def search(
        self,
        *,
        query: str | None = None,
        path: str | None = None,
        content_type: str | None = None,
        subjects: list[str] | None = None,
        limit: int = 10,
    ) -> list[ContentNode]:
        return self._search_sync(
            query=query,
            path=path,
            content_type=content_type,
            subjects=subjects,
            limit=limit,
        )

    # --- Documents ---

    def add_chunks(self, chunks: list[DocumentChunk]) -> None:
        """Add pre-indexed document chunks for search_documents."""
        self._chunks.extend(chunks)

    async def search_documents(
        self,
        query: str,
        *,
        path: str | None = None,
        limit: int = 5,
    ) -> list[DocumentChunk]:
        terms = query.lower().split()
        scored: list[tuple[int, DocumentChunk]] = []
        for chunk in self._chunks:
            if path is not None:
                if not _is_under(chunk.source_path, path) and chunk.source_path != path:
                    continue
            text_lower = chunk.text.lower()
            hits = sum(1 for t in terms if t in text_lower)
            if hits > 0:
                scored.append((hits, chunk))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [chunk for _, chunk in scored[:limit]]

    async def read_document_pages(
        self,
        path: str,
        *,
        start_page: int = 1,
        end_page: int = 5,
    ) -> Result:
        pages = self._document_pages.get(path)
        if pages is None:
            return _err(f"No page-level content available for '{path}'.")
        total = len(pages)
        # Clamp to valid range (1-indexed input, 0-indexed storage)
        start = max(1, start_page)
        end = min(total, end_page)
        if start > total:
            return _err(f"Start page {start} exceeds document length ({total} pages).")
        texts = pages[start - 1 : end]
        return _ok(
            f"Pages {start}–{end} of {total}.",
            data={
                "path": path,
                "start_page": start,
                "end_page": end,
                "total_pages": total,
                "content": "\n\n".join(texts),
            },
        )

    # --- Images ---

    async def resolve_image(self, path: str, *, scale: str = "large") -> str | None:
        node = self._nodes.get(path)
        if node is None:
            return None
        if node.preview_image:
            return node.preview_image
        return None

    # --- Page layout read ---

    def _refresh_listings(self, blocks: list[Any]) -> None:
        """Re-resolve listing blocks so they reflect current content."""
        for block in blocks:
            if block.type == "listing":
                self._resolve_listing_children(block)
            elif hasattr(block, "children") and block.children:
                self._refresh_listings(block.children)

    def _refresh_all_listings(self) -> None:
        """Re-resolve listings across all pages if content tree changed."""
        if not self._listings_dirty:
            return
        for shadow in self._pages.values():
            self._refresh_listings(shadow.layout.root)
        self._listings_dirty = False

    async def get_layout(
        self, page: str, *, path: str = "/", name: str | None = None
    ) -> Result:
        shadow = self._get_page(page)
        if isinstance(shadow, Result):
            return shadow
        self._refresh_all_listings()
        container = self._resolve_container(shadow.layout, path)
        if isinstance(container, Result):
            return container
        if name is not None:
            found = _find(container, name)
            if found is None:
                return _err(
                    f"'{name}' not found at {path}. Available: {', '.join(_names(container))}."
                )
            return _ok(f"Element '{name}'.", data=found[1].model_dump())
        return _ok(
            f"{len(container)} element(s) at {path}.",
            data=[b.model_dump() for b in container],
        )

    async def get_metadata(self, page: str) -> Result:
        shadow = self._get_page(page)
        if isinstance(shadow, Result):
            return shadow
        data = shadow.metadata.model_dump()
        node = self._nodes.get(page)
        if node is not None:
            data["content_type"] = node.content_type
        return _ok("Page metadata.", data=data)

    # --- Page layout write ---

    async def create_element(
        self,
        page: str,
        *,
        block_type: str,
        path: str,
        name: str,
        attributes: dict[str, Any],
        after: str | None = None,
        before: str | None = None,
        to_start: bool = False,
    ) -> Result:
        guard = self._check_current_page(page)
        if guard is not None:
            return guard
        shadow = self._get_page(page)
        if isinstance(shadow, Result):
            return shadow

        type_entry = BLOCK_TYPES.get(block_type)
        if type_entry is None:
            return _err(f"Unknown block type '{block_type}'.")
        attrs_model, _ = type_entry

        container = self._resolve_container(shadow.layout, path)
        if isinstance(container, Result):
            return container

        pc_err = self._validate_parent_child(shadow.layout, path, block_type)
        if pc_err is not None:
            return pc_err

        if _find(container, name) is not None:
            return _err(f"Name '{name}' already exists at {path}.")

        validated = _validate(
            attrs_model, attributes, f"Invalid attributes for {block_type}"
        )
        if isinstance(validated, Result):
            return validated

        pos = _resolve_position(container, after, before, to_start)
        if isinstance(pos, Result):
            return pos

        block = self._build_block(block_type, attrs_model, path, name, validated)
        container.insert(pos, block)

        if block_type == "column":
            width_err = self._check_column_width(shadow.layout, path)
            if width_err is not None:
                container.remove(block)
                return width_err

        if block_type in ("title", "description"):
            self._sync_metadata_from_block(shadow, block_type, validated.model_dump())

        # Auto-resolve listing
        if block_type == "listing":
            await self._resolve_listing_block(block, shadow)

        return _ok(f"Created {block_type} '{name}' at {path}.", data=block.model_dump())

    async def update_element(
        self, page: str, *, path: str, name: str, attributes: dict[str, Any]
    ) -> Result:
        guard = self._check_current_page(page)
        if guard is not None:
            return guard
        shadow = self._get_page(page)
        if isinstance(shadow, Result):
            return shadow

        container = self._resolve_container(shadow.layout, path)
        if isinstance(container, Result):
            return container

        found = _find(container, name)
        if found is None:
            return _err(
                f"'{name}' not found at {path}. Available: {', '.join(_names(container))}."
            )
        _, block = found

        type_entry = BLOCK_TYPES.get(block.type)
        if type_entry is None:
            return _err(f"Unknown block type '{block.type}'.")
        attrs_model, update_model = type_entry

        patch = _validate(update_model, attributes, f"Invalid update for {block.type}")
        if isinstance(patch, Result):
            return patch

        merged = block.attributes.model_dump()
        patch_fields = patch.model_dump(exclude_none=True)
        merged.update(patch_fields)

        new_attrs = _validate(
            attrs_model, merged, f"Merged attributes invalid for {block.type}"
        )
        if isinstance(new_attrs, Result):
            return new_attrs

        old_attrs = block.attributes
        block.attributes = new_attrs

        if block.type == "column":
            width_err = self._check_column_width(shadow.layout, path)
            if width_err is not None:
                block.attributes = old_attrs
                return width_err

        if block.type in ("title", "description"):
            self._sync_metadata_from_block(shadow, block.type, merged)

        if block.type == "listing":
            await self._resolve_listing_block(block, shadow)

        return _ok(f"Updated '{name}' at {path}.", data=block.model_dump())

    async def delete_element(self, page: str, *, path: str, name: str) -> Result:
        guard = self._check_current_page(page)
        if guard is not None:
            return guard
        shadow = self._get_page(page)
        if isinstance(shadow, Result):
            return shadow

        container = self._resolve_container(shadow.layout, path)
        if isinstance(container, Result):
            return container

        found = _find(container, name)
        if found is None:
            return _err(
                f"'{name}' not found at {path}. Available: {', '.join(_names(container))}."
            )
        idx, block = found
        container.pop(idx)

        if block.type == "column":
            width_err = self._check_column_width(shadow.layout, path)
            if width_err is not None:
                container.insert(idx, block)
                return width_err

        return _ok(f"Deleted '{name}' from {path}.")

    async def swap_elements(
        self,
        page: str,
        *,
        path_a: str,
        name_a: str,
        path_b: str,
        name_b: str,
    ) -> Result:
        guard = self._check_current_page(page)
        if guard is not None:
            return guard
        shadow = self._get_page(page)
        if isinstance(shadow, Result):
            return shadow

        cont_a = self._resolve_container(shadow.layout, path_a)
        if isinstance(cont_a, Result):
            return cont_a
        found_a = _find(cont_a, name_a)
        if found_a is None:
            return _err(
                f"'{name_a}' not found at {path_a}. Available: {', '.join(_names(cont_a))}."
            )

        cont_b = self._resolve_container(shadow.layout, path_b)
        if isinstance(cont_b, Result):
            return cont_b
        found_b = _find(cont_b, name_b)
        if found_b is None:
            return _err(
                f"'{name_b}' not found at {path_b}. Available: {', '.join(_names(cont_b))}."
            )

        idx_a, block_a = found_a
        idx_b, block_b = found_b

        # Validate parent-child compatibility in the swapped positions.
        pc_err = self._validate_parent_child(shadow.layout, path_a, block_b.type)
        if pc_err is not None:
            return pc_err
        pc_err = self._validate_parent_child(shadow.layout, path_b, block_a.type)
        if pc_err is not None:
            return pc_err

        # Perform the swap.
        cont_a[idx_a] = block_b
        cont_b[idx_b] = block_a
        _update_paths(block_a, path_b)
        _update_paths(block_b, path_a)

        # Validate column width constraints if columns are involved.
        if block_a.type == "column" or block_b.type == "column":
            for check_path in {path_a, path_b}:
                width_err = self._check_column_width(shadow.layout, check_path)
                if width_err is not None:
                    # Rollback.
                    cont_a[idx_a] = block_a
                    cont_b[idx_b] = block_b
                    _update_paths(block_a, path_a)
                    _update_paths(block_b, path_b)
                    return width_err

        return _ok(f"Swapped '{name_a}' ({path_a}) and '{name_b}' ({path_b}).")

    async def move_element(
        self,
        page: str,
        *,
        path: str,
        name: str,
        to_path: str,
        after_name: str | None = None,
        before_name: str | None = None,
        to_start: bool = False,
        new_name: str | None = None,
    ) -> Result:
        guard = self._check_current_page(page)
        if guard is not None:
            return guard
        shadow = self._get_page(page)
        if isinstance(shadow, Result):
            return shadow

        src = self._resolve_container(shadow.layout, path)
        if isinstance(src, Result):
            return src
        found = _find(src, name)
        if found is None:
            return _err(
                f"'{name}' not found at {path}. Available: {', '.join(_names(src))}."
            )
        src_idx, block = found

        block_abs = (path.rstrip("/") + "/" + name) if path != "/" else ("/" + name)
        if to_path == block_abs or to_path.startswith(block_abs + "/"):
            return _err(f"Cannot move '{name}' into its own subtree.")

        dst = self._resolve_container(shadow.layout, to_path)
        if isinstance(dst, Result):
            return dst

        pc_err = self._validate_parent_child(shadow.layout, to_path, block.type)
        if pc_err is not None:
            return pc_err

        same = dst is src
        final_name = new_name or name
        existing = _find(dst, final_name)
        if existing is not None and not (same and final_name == name):
            return _err(f"Name '{final_name}' already exists at {to_path}.")

        pos = _resolve_position(dst, after_name, before_name, to_start)
        if isinstance(pos, Result):
            return pos

        src.pop(src_idx)
        if same and src_idx < pos:
            pos -= 1
        if new_name:
            block.name = new_name
        _update_paths(block, to_path)
        dst.insert(pos, block)

        if block.type == "column":
            width_err = self._check_column_width(shadow.layout, to_path)
            if width_err is not None:
                dst.remove(block)
                block.name = name
                _update_paths(block, path)
                src.insert(src_idx, block)
                return width_err

        return _ok(f"Moved '{name}' from {path} to {to_path}.")

    async def copy_element(
        self,
        page: str,
        *,
        source_page: str | None = None,
        path: str,
        name: str,
        to_path: str,
        after_name: str | None = None,
        before_name: str | None = None,
        to_start: bool = False,
        new_name: str | None = None,
    ) -> Result:
        guard = self._check_current_page(page)
        if guard is not None:
            return guard

        src_page = source_page or page

        src_shadow = self._get_page(src_page)
        if isinstance(src_shadow, Result):
            return src_shadow
        src = self._resolve_container(src_shadow.layout, path)
        if isinstance(src, Result):
            return src
        found = _find(src, name)
        if found is None:
            return _err(
                f"'{name}' not found at {path}. Available: {', '.join(_names(src))}."
            )
        _, block = found

        dst_shadow = src_shadow if src_page == page else self._get_page(page)
        if isinstance(dst_shadow, Result):
            return dst_shadow
        dst = self._resolve_container(dst_shadow.layout, to_path)
        if isinstance(dst, Result):
            return dst

        pc_err = self._validate_parent_child(dst_shadow.layout, to_path, block.type)
        if pc_err is not None:
            return pc_err

        final_name = new_name or name
        if _find(dst, final_name) is not None:
            return _err(f"Name '{final_name}' already exists at {to_path}.")

        clone = _deep_copy(block)
        clone.name = final_name
        _update_paths(clone, to_path)

        pos = _resolve_position(dst, after_name, before_name, to_start)
        if isinstance(pos, Result):
            return pos

        dst.insert(pos, clone)
        if clone.type == "column":
            width_err = self._check_column_width(dst_shadow.layout, to_path)
            if width_err is not None:
                dst.remove(clone)
                return width_err

        return _ok(
            f"Copied '{name}' to {to_path} as '{final_name}'.", data=clone.model_dump()
        )

    async def update_metadata(self, page: str, *, attributes: dict[str, Any]) -> Result:
        guard = self._check_current_page(page)
        if guard is not None:
            return guard
        shadow = self._get_page(page)
        if isinstance(shadow, Result):
            return shadow

        patch = _validate(MetadataUpdate, attributes, "Invalid metadata")
        if isinstance(patch, Result):
            return patch

        updates = patch.model_dump(exclude_none=True)
        if not updates:
            return _err("No metadata fields provided.")

        for key, value in updates.items():
            setattr(shadow.metadata, key, value)

        if "title" in updates:
            self._walk_and_sync(
                cast(BlockContainer, shadow.layout.root),
                "title",
                TitleAttributes(text=shadow.metadata.title),
            )
        if "description" in updates:
            self._walk_and_sync(
                cast(BlockContainer, shadow.layout.root),
                "description",
                DescriptionAttributes(text=shadow.metadata.description),
            )

        # Sync metadata back to the content node so listings reflect changes.
        node = self._nodes.get(page)
        if node is not None:
            for key in (
                "title",
                "description",
                "preview_image",
                "subjects",
                "start",
                "end",
            ):
                if key in updates:
                    setattr(node, key, getattr(shadow.metadata, key))
            self._listings_dirty = True

        return _ok(
            f"Updated metadata: {', '.join(updates)}.",
            data=shadow.metadata.model_dump(),
        )

    # --- Internal helpers ---

    def _resolve_container(self, layout: Layout, path: str) -> BlockContainer | Result:
        if path == "/":
            return cast(BlockContainer, layout.root)
        segments = path.strip("/").split("/")
        current: BlockContainer = cast(BlockContainer, layout.root)
        for i, segment in enumerate(segments):
            found = _find(current, segment)
            if found is None:
                ctx = "/" + "/".join(segments[:i]) if i > 0 else "/"
                return _err(
                    f"'{segment}' not found at {ctx}. Available: {', '.join(_names(current))}."
                )
            _, block = found
            if not hasattr(block, "children"):
                return _err(f"'{segment}' ({block.type}) cannot contain children.")
            current = cast(BlockContainer, block.children)
        return current

    def _validate_parent_child(
        self, layout: Layout, path: str, child_type: str
    ) -> Result | None:
        if path == "/":
            if child_type in CHILD_ONLY_TYPES:
                return _err(
                    f"'{child_type}' must be inside a {PARENT_NEEDED[child_type]}."
                )
            return None
        segments = path.strip("/").split("/")
        current: BlockContainer = cast(BlockContainer, layout.root)
        container_block = None
        for segment in segments:
            found = _find(current, segment)
            if found is None:
                return _err(f"Container '{segment}' not found.")
            _, container_block = found
            if not hasattr(container_block, "children"):
                return _err(
                    f"'{segment}' ({container_block.type}) cannot contain children."
                )
            current = cast(BlockContainer, container_block.children)
        if container_block is None:
            return None
        ct = container_block.type
        if ct in CHILD_TYPES:
            if child_type != CHILD_TYPES[ct]:
                return _err(
                    f"'{ct}' only accepts '{CHILD_TYPES[ct]}', not '{child_type}'."
                )
            return None
        if ct in RESTRICTED_CHILD_TYPES:
            if child_type not in RESTRICTED_CHILD_TYPES[ct]:
                allowed = ", ".join(sorted(RESTRICTED_CHILD_TYPES[ct]))
                return _err(f"'{ct}' only accepts [{allowed}], not '{child_type}'.")
            return None
        if ct in OPEN_CONTAINER_TYPES:
            if child_type in CHILD_ONLY_TYPES:
                return _err(
                    f"'{child_type}' must be inside a {PARENT_NEEDED[child_type]}."
                )
            return None
        return _err(f"'{ct}' cannot contain children.")

    def _check_column_width(self, layout: Layout, path: str) -> Result | None:
        segments = path.strip("/").split("/")
        current: BlockContainer = cast(BlockContainer, layout.root)
        container_block = None
        for segment in segments:
            found = _find(current, segment)
            if found is None:
                return None
            _, container_block = found
            if hasattr(container_block, "children"):
                current = cast(BlockContainer, container_block.children)
        if container_block is None or container_block.type != "columns":
            return None
        total = sum(col.attributes.width for col in container_block.children)
        if not 1 <= total <= 4:
            widths = ", ".join(
                f"{col.name}={col.attributes.width}" for col in container_block.children
            )
            return _err(f"Column width sum is {total} (must be 1–4). Widths: {widths}.")
        return None

    def _build_block(
        self,
        type_name: str,
        attrs_model: type[BaseModel],
        path: str,
        name: str,
        attrs: BaseModel,
    ) -> Any:
        block_model = BLOCK_MODELS[type_name]
        data: dict[str, Any] = {
            "type": type_name,
            "id": str(uuid.uuid4()),
            "path": path,
            "name": name,
            "attributes": attrs_model(**attrs.model_dump()),
        }
        if type_name in CONTAINER_TYPES:
            data["children"] = []
        return block_model.model_construct(**data)

    def _sync_metadata_from_block(
        self, shadow: _PageShadow, block_type: str, attrs: dict[str, Any]
    ) -> None:
        if block_type == "title" and "text" in attrs:
            shadow.metadata.title = attrs["text"]
        if block_type == "description" and "text" in attrs:
            shadow.metadata.description = attrs["text"]

    def _walk_and_sync(
        self, blocks: BlockContainer, block_type: str, attrs: BaseModel
    ) -> None:
        for block in blocks:
            if block.type == block_type:
                block.attributes = attrs
            if hasattr(block, "children"):
                self._walk_and_sync(block.children, block_type, attrs)

    def _resolve_listing_children(self, block: Any) -> None:
        """Populate a listing block's children from its query filters."""
        query: ListingQuery = block.attributes.query
        search_path: str | None = None
        search_type: str | None = None
        search_subjects: list[str] | None = None
        for f in query.filters:
            if isinstance(f, PathFilter) and f.paths:
                search_path = f.paths[0]
            elif isinstance(f, ContentTypeFilter) and f.content_types:
                search_type = f.content_types[0]
            elif isinstance(f, SubjectFilter) and f.subjects:
                search_subjects = f.subjects
        results = self._search_sync(
            path=search_path,
            content_type=search_type,
            subjects=search_subjects,
            limit=query.limit * 5,  # fetch extra so we can sort then trim
        )
        if query.sort_on:
            reverse = query.sort_order == "descending"

            def _sort_key(n: ContentNode) -> tuple[bool, Any]:
                v = getattr(n, query.sort_on, None)
                # Normalize aware datetimes to naive for consistent comparison.
                if isinstance(v, datetime) and v.tzinfo is not None:
                    v = v.replace(tzinfo=None)
                return (v is None, v or "")

            results.sort(key=_sort_key, reverse=reverse)
        results = results[: query.limit]
        listing_path = (
            block.path.rstrip("/") + "/" + block.name
            if block.path != "/"
            else "/" + block.name
        )
        block.children = [
            ListingItemBlock(
                type="listing_item",
                id=str(uuid.uuid4()),
                path=listing_path,
                name=f"listing_item_{i + 1}",
                attributes=ListingItemAttributes(
                    content_path=node.path,
                    title=node.title,
                    description=node.description,
                    content_type=node.content_type,
                    preview_image=node.preview_image,
                    published=node.published,
                ),
            )
            for i, node in enumerate(results)
        ]

    # Alias used by from_json (sync context).
    _resolve_listing_block_sync = _resolve_listing_children

    async def _resolve_listing_block(self, block: Any, shadow: _PageShadow) -> None:
        self._resolve_listing_children(block)
