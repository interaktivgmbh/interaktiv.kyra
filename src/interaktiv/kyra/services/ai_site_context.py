"""Pre-load site context from Plone catalog for the layout-agent proxy.

When the layout-agent runs remotely and cannot call back to Plone,
this module provides a site snapshot AND document content that gets
injected into the first user message — so the agent knows about the
site structure, available pages, and can answer questions about documents.
"""

from __future__ import annotations

import io
import logging
from typing import Any

from plone import api

logger = logging.getLogger(__name__)

# Maximum text per document to avoid blowing up the context
_MAX_DOC_TEXT = 8000
_MAX_TOTAL_DOC_TEXT = 40000


def _extract_pdf_text(data: bytes) -> tuple[str, list[str]]:
    """Extract text from PDF bytes. Returns (full_text, per_page_texts)."""
    pages: list[str] = []

    try:
        import PyPDF2

        reader = PyPDF2.PdfReader(io.BytesIO(data))
        for p in reader.pages:
            try:
                pages.append(p.extract_text() or "")
            except Exception:
                pages.append("")
        full = "\n\n".join(pages)
        return full.strip(), pages
    except ImportError:
        pass
    except Exception:
        pass

    try:
        from pdfminer.high_level import extract_text

        full = extract_text(io.BytesIO(data)).strip()
        return full, []
    except Exception:
        pass

    return "", []


def _extract_file_text(obj: Any) -> tuple[str, list[str]]:
    """Extract text content from a Plone File object.

    Returns (full_text, per_page_texts).
    """
    file_field = getattr(obj, "file", None)
    if file_field is None:
        return "", []

    data = file_field.data
    if not data:
        return "", []

    content_type = getattr(file_field, "contentType", "") or ""

    if "pdf" in content_type:
        return _extract_pdf_text(data)

    if content_type.startswith("text/"):
        text = data.decode("utf-8", errors="ignore").strip()
        return text, []

    return "", []


def build_site_context(page_path: str = "") -> str:
    """Build a text summary of the site structure + document content.

    Includes:
    - Page tree (title, path, type)
    - Available documents with their extracted text content
    """
    parts: list[str] = []

    try:
        catalog = api.portal.get_tool("portal_catalog")
        portal = api.portal.get()
        portal_path = "/".join(portal.getPhysicalPath())

        brains = catalog.searchResults(
            sort_on="path",
            sort_limit=200,
        )

        pages: list[str] = []
        documents: list[dict] = []

        for brain in brains[:200]:
            try:
                full_path = brain.getPath()
                rel_path = full_path[len(portal_path):] or "/"
                title = brain.Title or ""
                portal_type = brain.portal_type or ""
                desc = brain.Description or ""

                if portal_type == "Plone Site":
                    continue

                label = f"- {rel_path}"
                if title:
                    label += f" — {title}"
                if portal_type:
                    label += f" ({portal_type})"
                if desc:
                    label += f": {desc[:100]}"

                pages.append(label)

                if portal_type in ("File",):
                    documents.append({
                        "brain": brain,
                        "path": rel_path,
                        "title": title,
                    })

            except Exception:
                continue

        if pages:
            parts.append(
                "Verfügbare Seiten und Inhalte auf dieser Website:\n"
                + "\n".join(pages)
            )

        if documents:
            doc_parts: list[str] = []
            total_text = 0

            for doc_info in documents:
                if total_text >= _MAX_TOTAL_DOC_TEXT:
                    doc_parts.append(
                        "(Weitere Dokumente vorhanden, aber Kontextlimit erreicht.)"
                    )
                    break

                try:
                    obj = doc_info["brain"].getObject()
                    full_text, page_texts = _extract_file_text(obj)

                    if not full_text:
                        doc_parts.append(
                            f"### {doc_info['title'] or doc_info['path']}\n"
                            f"Pfad: {doc_info['path']}\n"
                            f"(Textextraktion fehlgeschlagen)"
                        )
                        continue

                    remaining = _MAX_TOTAL_DOC_TEXT - total_text
                    max_for_this = min(_MAX_DOC_TEXT, remaining)

                    header = (
                        f"### {doc_info['title'] or doc_info['path']}\n"
                        f"Pfad: {doc_info['path']}\n"
                    )

                    if page_texts:
                        header += f"Gesamtseiten: {len(page_texts)}\n\n"
                        page_content: list[str] = []
                        chars_used = 0
                        for i, page_text in enumerate(page_texts, 1):
                            page_text = page_text.strip()
                            if not page_text:
                                continue
                            entry = f"--- Seite {i} ---\n{page_text}"
                            if chars_used + len(entry) > max_for_this:
                                page_content.append(
                                    f"(... weitere Seiten gekürzt, "
                                    f"{len(page_texts) - i + 1} Seiten verbleibend)"
                                )
                                break
                            page_content.append(entry)
                            chars_used += len(entry)
                        header += "\n".join(page_content)
                        total_text += chars_used
                    else:
                        truncated = len(full_text) > max_for_this
                        text = full_text[:max_for_this]
                        total_text += len(text)
                        header += f"Inhalt:\n{text}"
                        if truncated:
                            header += "\n(... Text gekürzt)"

                    doc_parts.append(header)

                except Exception:
                    logger.debug(
                        "[ai-site-context] Failed to extract text from %s",
                        doc_info["path"],
                        exc_info=True,
                    )

            if doc_parts:
                parts.append(
                    "Verfügbare Dokumente mit Inhalt:\n\n"
                    + "\n\n".join(doc_parts)
                )

    except Exception:
        logger.debug("[ai-site-context] Failed to load site context", exc_info=True)

    return "\n\n".join(parts)
