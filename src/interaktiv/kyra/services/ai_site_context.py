"""Pre-load site context from Plone catalog for the layout-agent proxy.

Provides a site-tree snapshot that gets injected into the first user
message so the agent knows about available pages and content structure.
Document content (PDFs etc.) is NOT included — the agent has callback
tools (read_document_pages, search_documents) for that.
"""

from __future__ import annotations

import logging

from plone import api

logger = logging.getLogger(__name__)


def build_site_context(page_path: str = "") -> str:
    """Build a text summary of the site structure.

    Includes only the page tree (title, path, type, description).
    Document content is left to the agent's callback tools.
    """
    try:
        catalog = api.portal.get_tool("portal_catalog")
        portal = api.portal.get()
        portal_path = "/".join(portal.getPhysicalPath())

        brains = catalog.searchResults(
            sort_on="path",
            sort_limit=200,
        )

        pages: list[str] = []

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

            except Exception:
                continue

        if pages:
            return (
                "Verfügbare Seiten und Inhalte auf dieser Website:\n"
                + "\n".join(pages)
            )

    except Exception:
        logger.debug("[ai-site-context] Failed to load site context", exc_info=True)

    return ""
