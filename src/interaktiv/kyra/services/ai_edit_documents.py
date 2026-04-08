"""Document indexing and search tools for the integrated Layout Agent.

At conversation creation time (in the Plone request thread), this module
fetches File/Document objects from the content tree, extracts text,
chunks it, and returns LangChain tools that the async agent can use.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from typing import Any

from langchain_core.tools import tool
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class DocumentChunk:
    source_path: str
    source_title: str
    text: str
    page: int | None = None


@dataclass
class DocumentStore:
    """In-memory store for document chunks, populated from Plone at conversation start."""

    chunks: list[DocumentChunk] = field(default_factory=list)
    document_pages: dict[str, list[str]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Text extraction (reuses patterns from ai_chat_upload.py)
# ---------------------------------------------------------------------------

_splitter: RecursiveCharacterTextSplitter | None = None


def _get_splitter() -> RecursiveCharacterTextSplitter:
    global _splitter
    if _splitter is None:
        _splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000, chunk_overlap=200
        )
    return _splitter


def _chunk_text(text: str, path: str, title: str) -> list[DocumentChunk]:
    splitter = _get_splitter()
    docs = splitter.create_documents([text])
    return [
        DocumentChunk(
            source_path=path,
            source_title=title,
            text=doc.page_content,
            page=i + 1 if len(docs) > 1 else None,
        )
        for i, doc in enumerate(docs)
    ]


def _extract_pdf_text(data: bytes) -> tuple[str, list[str] | None]:
    """Extract text from PDF bytes. Returns (full_text, per_page_texts)."""
    pages: list[str] = []

    # Try pdfminer first
    try:
        from pdfminer.high_level import extract_text_by_page  # type: ignore
        from pdfminer.high_level import extract_text  # type: ignore

        full = extract_text(io.BytesIO(data)).strip()
        if full:
            # Try page-level extraction
            try:
                import PyPDF2  # type: ignore

                reader = PyPDF2.PdfReader(io.BytesIO(data))
                for p in reader.pages:
                    try:
                        pages.append(p.extract_text() or "")
                    except Exception:
                        pages.append("")
            except Exception:
                pass
            return full, pages if pages else None
    except Exception:
        pass

    # Fallback: PyPDF2
    try:
        import PyPDF2  # type: ignore

        reader = PyPDF2.PdfReader(io.BytesIO(data))
        for p in reader.pages:
            try:
                text = p.extract_text() or ""
                pages.append(text)
            except Exception:
                pages.append("")
        full = "\n\n".join(pages)
        return full.strip(), pages
    except Exception:
        pass

    return "", None


# ---------------------------------------------------------------------------
# Plone document loading
# ---------------------------------------------------------------------------


def load_documents_from_plone(context_path: str = "/") -> DocumentStore:
    """Fetch File objects from the Plone catalog and index them.

    Must be called from a Plone request thread (needs catalog access).
    Returns a DocumentStore ready for use by the agent tools.
    """
    from plone import api

    store = DocumentStore()
    try:
        portal = api.portal.get()
        catalog = api.portal.get_tool("portal_catalog")

        # Search for File content types in the site
        portal_path = "/".join(portal.getPhysicalPath())
        query = {
            "portal_type": ["File"],
            "path": {"query": portal_path},
        }
        logger.warning("[ai-edit-docs] Searching for documents in %s", portal_path)
        brains = catalog.searchResults(**query)
        logger.warning("[ai-edit-docs] Found %d file objects", len(brains))

        for brain in brains:
            try:
                obj = brain.getObject()
                path = "/" + "/".join(obj.getPhysicalPath()[2:])  # strip portal id
                title = obj.Title() or brain.Title or path.split("/")[-1]

                # Get file data
                file_field = getattr(obj, "file", None)
                if file_field is None:
                    logger.warning("[ai-edit-docs] %s has no file field, skipping", path)
                    continue

                data = file_field.data
                if not data:
                    logger.warning("[ai-edit-docs] %s has empty file data, skipping", path)
                    continue

                content_type = getattr(file_field, "contentType", "") or ""
                logger.warning("[ai-edit-docs] Processing %s (%s, %d bytes)", path, content_type, len(data))

                if "pdf" in content_type:
                    full_text, pages = _extract_pdf_text(data)
                    if full_text:
                        store.chunks.extend(_chunk_text(full_text, path, title))
                        if pages:
                            store.document_pages[path] = pages
                        logger.info(
                            "[ai-edit-docs] Indexed PDF: %s (%d chars, %d chunks)",
                            path, len(full_text), len(store.chunks),
                        )
                elif content_type.startswith("text/"):
                    text = data.decode("utf-8", errors="ignore").strip()
                    if text:
                        store.chunks.extend(_chunk_text(text, path, title))
                        logger.info(
                            "[ai-edit-docs] Indexed text file: %s (%d chars)",
                            path, len(text),
                        )
            except Exception:
                logger.debug("[ai-edit-docs] Failed to index %s", brain.getPath(), exc_info=True)

        logger.info(
            "[ai-edit-docs] Document indexing complete: %d chunks from %d files",
            len(store.chunks), len(brains),
        )
    except Exception:
        logger.warning("[ai-edit-docs] Document indexing failed", exc_info=True)

    return store


# ---------------------------------------------------------------------------
# LangChain tools
# ---------------------------------------------------------------------------


class SearchDocumentsInput(BaseModel):
    query: str = Field(description="Search query (keywords)")
    path: str | None = Field(default=None, description="Restrict to subtree")
    limit: int = Field(default=5, description="Max results")


class ReadDocumentPagesInput(BaseModel):
    path: str = Field(description="Content path of the document")
    start_page: int = Field(default=1, description="First page (1-indexed)")
    end_page: int = Field(default=5, description="Last page (1-indexed, max 5 pages per request)")


def make_document_tools(doc_store: DocumentStore) -> list:
    """Create document search/read tools bound to a DocumentStore."""

    if not doc_store.chunks:
        return []

    @tool(args_schema=SearchDocumentsInput)
    def search_documents(
        query: str, path: str | None = None, limit: int = 5
    ) -> str:
        """Search within documents (PDFs, files) stored on the site. Returns
        text chunks from documents that match the query, with source path and
        page number. Use this to find specific information in PDFs or
        long documents — e.g. regulations, forms, reports."""
        import json

        terms = query.lower().split()
        scored: list[tuple[int, DocumentChunk]] = []
        for chunk in doc_store.chunks:
            if path is not None:
                if not chunk.source_path.startswith(path.rstrip("/") + "/") and chunk.source_path != path:
                    continue
            text_lower = chunk.text.lower()
            hits = sum(1 for t in terms if t in text_lower)
            if hits > 0:
                scored.append((hits, chunk))
        scored.sort(key=lambda x: x[0], reverse=True)
        results = scored[:limit]

        if not results:
            return json.dumps({"results": [], "message": "No matching document content found."})

        return json.dumps({
            "results": [
                {
                    "source": c.source_path,
                    "title": c.source_title,
                    "text": c.text,
                    **({"page": c.page} if c.page is not None else {}),
                }
                for _, c in results
            ],
            "count": len(results),
        }, ensure_ascii=False, default=str)

    @tool(args_schema=ReadDocumentPagesInput)
    def read_document_pages(
        path: str, start_page: int = 1, end_page: int = 5
    ) -> str:
        """Read specific pages from a document (PDF). Returns the full text
        content of the requested page range. Use this after search_documents to
        read surrounding context, or to browse a document section by section.
        Maximum 5 pages per request."""
        import json

        if end_page - start_page >= 5:
            end_page = start_page + 4

        pages = doc_store.document_pages.get(path)
        if pages is None:
            return json.dumps({"ok": False, "message": f"No page-level content available for '{path}'."})

        total = len(pages)
        start = max(1, start_page)
        end = min(total, end_page)
        if start > total:
            return json.dumps({"ok": False, "message": f"Start page {start} exceeds document length ({total} pages)."})

        texts = pages[start - 1: end]
        return json.dumps({
            "ok": True,
            "message": f"Pages {start}-{end} of {total}.",
            "path": path,
            "start_page": start,
            "end_page": end,
            "total_pages": total,
            "content": "\n\n".join(texts),
        }, ensure_ascii=False, default=str)

    return [search_documents, read_document_pages]
