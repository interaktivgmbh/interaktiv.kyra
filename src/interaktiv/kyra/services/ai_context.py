import html as _html_module
import re

from plone import api
from typing import Any, Dict, List, Optional, Tuple

MAX_PAGE_TEXT = 15000
MAX_DOC_TEXT = 4000
MAX_RELATED_DOCS = 6
MAX_SITE_DOCS = 3
HTML_TOKEN_RE = re.compile(r"\b(?:p|li|ul|ol|h[1-6])\b", re.IGNORECASE)

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(value: str) -> str:
    text = _html_module.unescape(value or "")
    text = _HTML_TAG_RE.sub("", text)
    return " ".join(text.split())


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "..."


def _flatten_block_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        parts: List[str] = []
        for child in value.values():
            text = _flatten_block_value(child)
            if text:
                parts.append(text)
        return " ".join(parts)
    if isinstance(value, (list, tuple, set)):
        parts: List[str] = []
        for child in value:
            text = _flatten_block_value(child)
            if text:
                parts.append(text)
        return " ".join(parts)
    return ""


def _flatten_slate_children(children: Any) -> str:
    if not isinstance(children, list):
        return ""
    parts: List[str] = []
    for child in children:
        if isinstance(child, dict):
            text = child.get("text") or ""
            if text:
                parts.append(text)
            # recurse into nested children
            nested = _flatten_slate_children(child.get("children"))
            if nested:
                parts.append(nested)
        elif isinstance(child, str):
            parts.append(child)
    return " ".join(parts)


def resolve_content(context_page: Optional[Dict[str, str]]) -> Tuple[Any, str]:
    portal = api.portal.get()
    if context_page:
        uid = context_page.get("uid")
        url = context_page.get("url")
        if uid:
            obj = api.content.get(UID=uid)
            if obj is not None:
                return obj, f"UID:{uid}"
        if url:
            portal_url = portal.absolute_url()
            path = url
            if url.startswith(portal_url):
                path = url[len(portal_url):]
            path = path.strip("/")
            if path:
                obj = api.content.get(path=path)
                if obj is not None:
                    return obj, f"path:/{path}"
    return portal, "portal-root"


def _collect_quotes(block_items: List[Any]) -> List[Dict[str, str]]:
    quotes: List[Dict[str, str]] = []
    for block in block_items:
        if not isinstance(block, dict):
            continue
        quote_text = block.get("quote") or block.get("text") or block.get("value")
        attribution = (
            block.get("attribution") or block.get("source") or block.get("cite") or block.get("citation")
        )
        # top-level quote block
        if isinstance(quote_text, str):
            cleaned_quote = strip_html(quote_text)
            if cleaned_quote:
                quotes.append(
                    {
                        "quote": cleaned_quote,
                        "attribution": strip_html(attribution) if isinstance(attribution, str) else "",
                    }
                )
        # Slate quote block (common pattern)
        slate_value = block.get("value")
        if isinstance(slate_value, dict):
            data = slate_value.get("data") or {}
            if isinstance(data, dict):
                quote = data.get("text") or data.get("quote")
                cite = data.get("citation") or data.get("attribution") or data.get("source")
                if isinstance(quote, str):
                    parts.append(strip_html(quote))
                if isinstance(cite, str):
                    parts.append(strip_html(cite))
                if isinstance(quote, str):
                    cleaned_quote = strip_html(quote)
                    if cleaned_quote:
                        quotes.append(
                            {
                                "quote": cleaned_quote,
                                "attribution": strip_html(cite) if isinstance(cite, str) else "",
                            }
                        )
        # Slate richtext children
        if "children" in block:
            slate_text = _flatten_slate_children(block.get("children"))
            if slate_text:
                # Always include in page text parts
                parts.append(strip_html(slate_text))
                if "quote" in (block.get("@type") or "").lower():
                    quotes.append({"quote": strip_html(slate_text), "attribution": ""})
        # Generic quote @type with text/citation
        btype = (block.get("@type") or "").lower()
        if "quote" in btype:
            for key in ("text", "quote", "value"):
                raw = block.get(key)
                if isinstance(raw, str):
                    cleaned_quote = strip_html(raw)
                    if cleaned_quote:
                        cite = block.get("citation") or block.get("attribution") or block.get("source") or ""
                        quotes.append(
                            {
                                "quote": cleaned_quote,
                                "attribution": strip_html(cite) if isinstance(cite, str) else "",
                            }
                        )
                        break
    return quotes


def extract_page_text(obj: Any) -> str:
    if obj is None:
        return ""
    parts: List[str] = []

    title = getattr(obj, "Title", None)
    if callable(title):
        parts.append(strip_html(str(title())))
    elif isinstance(title, str):
        parts.append(strip_html(title))

    description = getattr(obj, "Description", None)
    if callable(description):
        parts.append(strip_html(str(description())))
    elif isinstance(description, str):
        parts.append(strip_html(description))

    # Include body field if present
    body = getattr(obj, "text", None)
    if callable(body):
        body = body()
    if isinstance(body, str):
        parts.append(strip_html(body))
    elif hasattr(body, "output"):
        parts.append(strip_html(str(body.output)))

    blocks = getattr(obj, "blocks", None) or getattr(obj, "getBlocks", lambda: {})()
    blocks_layout = getattr(obj, "blocks_layout", None) or getattr(obj, "getBlocksLayout", lambda: {})()
    ordered_ids = []
    if isinstance(blocks_layout, dict):
        ordered_ids = blocks_layout.get("items") or []

    if isinstance(blocks, dict):
        block_items = []
        for bid in ordered_ids:
            if bid in blocks:
                block_items.append(blocks[bid])
        # fallback to all values
        if not block_items:
            block_items = list(blocks.values())

        for block in block_items:
            if not isinstance(block, dict):
                text = _flatten_block_value(block)
                if text:
                    parts.append(strip_html(text))
                continue

            # prefer "text" and "value" keys
            if "text" in block and isinstance(block.get("text"), str):
                parts.append(strip_html(block.get("text")))
            if "value" in block:
                text = _flatten_block_value(block.get("value"))
                if text:
                    parts.append(strip_html(text))
            # include common quote/description fields explicitly
            for key in ("quote", "source", "cite", "attribution", "description", "headline"):
                if key in block and isinstance(block.get(key), str):
                    parts.append(strip_html(block.get(key)))

            # add any nested strings
            text = _flatten_block_value(block)
            if text:
                parts.append(strip_html(text))

    text = " ".join(filter(None, parts))
    text = _truncate(text, MAX_PAGE_TEXT)
    quotes = _collect_quotes(block_items if "block_items" in locals() else [])

    # Merge unique quotes
    seen_quotes = set()
    final_quotes: List[Dict[str, str]] = []
    for item in quotes:
        q = (item.get("quote") or "").strip()
        a = (item.get("attribution") or "").strip()
        key = (q.lower(), a.lower())
        if q and key not in seen_quotes:
            seen_quotes.add(key)
            final_quotes.append({"quote": q, "attribution": a})

    return text, final_quotes


def _call_if_callable(value: Any) -> Any:
    if callable(value):
        return value()
    return value


def _build_doc(
    doc_id: str,
    title: str,
    url: str,
    text: str,
    doc_type: str,
    score: float = 0.0,
) -> Dict[str, Any]:
    return {
        "id": str(doc_id),
        "title": str(title or url),
        "url": str(url),
        "text": _truncate(str(text or ""), MAX_DOC_TEXT),
        "type": doc_type,
        "score": float(score or 0.0),
    }


def catalog_related_docs(
    query: str,
    exclude_uid: Optional[str] = None,
    limit: int = MAX_RELATED_DOCS,
    doc_type: str = "related",
) -> List[Dict[str, Any]]:
    if not query:
        return []
    catalog = api.portal.get_tool("portal_catalog")
    results = catalog.searchResults(
        SearchableText=query,
        sort_on="effective",
        sort_order="reverse",
        limit=limit * 2,
    )
    docs: List[Dict[str, Any]] = []
    for brain in results:
        if exclude_uid and getattr(brain, "UID", None) == exclude_uid:
            continue
        url = getattr(brain, "getURL", lambda: "")()
        title = getattr(brain, "Title", "") or ""
        text = getattr(brain, "Description", "") or ""
        doc = _build_doc(
            doc_id=getattr(brain, "UID", "") or url,
            title=title,
            url=url,
            text=text,
            doc_type=doc_type,
            score=float(getattr(brain, "getScore", lambda: 0)() or 0.0),
        )
        docs.append(doc)
        if len(docs) >= limit:
            break
    return docs


def clean_text(value: str) -> str:
    text = strip_html(value or "")
    text = HTML_TOKEN_RE.sub(" ", text)
    return " ".join(text.split())


def _build_doc_from_obj(obj: Any, doc_type: str, score: float = 0.0) -> Dict[str, Any]:
    if obj is None:
        return {}

    doc_id = _call_if_callable(getattr(obj, "UID", None)) or _call_if_callable(getattr(obj, "id", ""))
    doc_url = _call_if_callable(getattr(obj, "absolute_url", lambda: "")())
    doc_title = _call_if_callable(getattr(obj, "Title", lambda: "")) or doc_url
    doc_text, _ = extract_page_text(obj)

    return _build_doc(
        doc_id=doc_id or doc_url,
        title=doc_title,
        url=str(doc_url),
        text=clean_text(doc_text),
        doc_type=doc_type,
        score=score,
    )


def collect_site_documents(page_doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    portal = api.portal.get()
    docs: List[Dict[str, Any]] = []
    portal_doc = _build_doc_from_obj(portal, doc_type="site", score=0.5)
    if portal_doc and portal_doc.get("id") and portal_doc.get("id") != page_doc.get("id"):
        docs.append(portal_doc)

    sections = api.content.find(
        context=portal,
        depth=1,
        sort_on="getObjPositionInParent",
        obj=True,
    )
    count = 0
    seen = {portal_doc.get("id"), page_doc.get("id")}
    for section in sections:
        if count >= MAX_SITE_DOCS:
            break
        section_id = _call_if_callable(getattr(section, "UID", None)) or getattr(section, "id", "")
        if not section_id or section_id in seen:
            continue
        seen.add(section_id)
        docs.append(
            _build_doc_from_obj(
                section,
                doc_type="site-section",
                score=0.4 - (count * 0.05),
            )
        )
        count += 1
    return docs


def build_context_documents(context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    mode = (context or {}).get("mode") or "page"
    page_info = (context or {}).get("page") or {}
    query = (context or {}).get("query") or ""
    selection_text = (context or {}).get("selection_text") or ""

    obj, resolved = resolve_content(page_info)
    raw_page_text, page_quotes = extract_page_text(obj)
    page_text = clean_text(raw_page_text)
    page_id = page_info.get("uid")
    if not page_id:
        page_id = _call_if_callable(getattr(obj, "UID", None)) or _call_if_callable(
            getattr(obj, "id", "")
        )
    page_url = _call_if_callable(getattr(obj, "absolute_url", lambda: "")())
    page_title = _call_if_callable(getattr(obj, "Title", lambda: "")())

    page_doc = _build_doc(
        doc_id=page_id or page_url,
        title=page_title or "",
        url=str(page_url),
        text=page_text,
        doc_type="page",
        score=1.0,
    )

    related_docs: List[Dict[str, Any]] = []
    if mode in ("related", "search"):
        keywords = query or page_title or page_info.get("title") or ""
        related_docs = catalog_related_docs(
            keywords,
            exclude_uid=page_doc["id"],
            limit=MAX_RELATED_DOCS,
            doc_type=mode,
        )

    site_docs = collect_site_documents(page_doc)
    documents = [page_doc] + site_docs + related_docs

    return {
        "mode": mode,
        "query": query,
        "selection_text": selection_text,
        "resolved": resolved,
        "page_text_length": len(raw_page_text),
        "documents": documents,
        "page_doc": page_doc,
        "related_docs": related_docs,
        "site_docs": site_docs,
        "quotes": page_quotes,
    }
