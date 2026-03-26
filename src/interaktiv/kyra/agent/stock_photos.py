"""Stock photo search tool for the layout agent.

Supports Pexels and Unsplash as backends. The agent sees a single
``search_stock_photos`` tool regardless of which service is configured.
Priority: Pexels > Unsplash (Pexels has better rate limits and alt text).
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from langchain_core.tools import tool

# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


def _search_pexels(
    query: str,
    orientation: str | None,
    per_page: int,
) -> list[dict[str, Any]]:
    api_key = os.environ["PEXELS_API_KEY"]
    params: dict[str, str | int] = {"query": query, "per_page": per_page}
    if orientation:
        params["orientation"] = orientation

    resp = httpx.get(
        "https://api.pexels.com/v1/search",
        params=params,
        headers={"Authorization": api_key},
        timeout=10.0,
    )
    resp.raise_for_status()

    results: list[dict[str, Any]] = []
    for photo in resp.json().get("photos", []):
        results.append(
            {
                "alt": photo.get("alt"),
                "url": photo["src"]["large"],
                "url_small": photo["src"]["medium"],
                "width": photo["width"],
                "height": photo["height"],
                "photographer": photo["photographer"],
            }
        )
    return results


def _search_unsplash(
    query: str,
    orientation: str | None,
    per_page: int,
) -> list[dict[str, Any]]:
    access_key = os.environ["UNSPLASH_ACCESS_KEY"]
    params: dict[str, str | int] = {
        "query": query,
        "per_page": per_page,
        "content_filter": "high",
    }
    if orientation:
        params["orientation"] = orientation

    resp = httpx.get(
        "https://api.unsplash.com/search/photos",
        params=params,
        headers={"Authorization": f"Client-ID {access_key}"},
        timeout=10.0,
    )
    resp.raise_for_status()
    data = resp.json()

    results: list[dict[str, Any]] = []
    for photo in data.get("results", []):
        user = photo["user"]
        results.append(
            {
                "alt": photo.get("alt_description") or photo.get("description"),
                "url": photo["urls"]["regular"],
                "url_small": photo["urls"]["small"],
                "width": photo["width"],
                "height": photo["height"],
                "photographer": user["name"],
            }
        )

        # Unsplash requires a download trigger when a photo is used.
        # We fire it eagerly here since the agent will likely use the result.
        _trigger_unsplash_download(access_key, photo["links"]["download_location"])

    return results


def _trigger_unsplash_download(access_key: str, download_location: str) -> None:
    try:
        httpx.get(
            download_location,
            headers={"Authorization": f"Client-ID {access_key}"},
            timeout=5.0,
        )
    except httpx.HTTPError:
        pass  # Best-effort; don't fail the search


# ---------------------------------------------------------------------------
# Resolve active backend
# ---------------------------------------------------------------------------

_Backend = tuple[str, Any]  # (name, search_fn)


def _get_backend() -> _Backend | None:
    if os.environ.get("PEXELS_API_KEY"):
        return ("pexels", _search_pexels)
    if os.environ.get("UNSPLASH_ACCESS_KEY"):
        return ("unsplash", _search_unsplash)
    return None


# ---------------------------------------------------------------------------
# LangChain tool
# ---------------------------------------------------------------------------


@tool
def search_stock_photos(
    query: str,
    orientation: str | None = None,
    per_page: int = 5,
) -> list[dict[str, Any]]:
    """Search for high-quality, free-to-use photos.

    Returns a list of photos with alt text, image URLs, dimensions,
    and photographer name. Use the ``url`` as ``image_url`` or
    ``preview_image`` in blocks, and ``alt`` as ``alt_text``.

    Args:
        query: Search terms, e.g. "mountain landscape", "office workspace".
        orientation: Optional — "landscape", "portrait", or "square".
        per_page: Number of results (1–10, default 5).
    """
    backend = _get_backend()
    if backend is None:
        return [{"error": "No stock photo service configured."}]

    _, search_fn = backend
    return search_fn(query, orientation, min(max(per_page, 1), 10))


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------


def make_stock_photo_tools() -> list:
    """Return the stock photo tool if a backend is configured."""
    if _get_backend() is None:
        return []
    return [search_stock_photos]
