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
            nested = _flatten_slate_children(child.get("children"))
            if nested:
                parts.append(nested)
        elif isinstance(child, str):
            parts.append(child)
    return " ".join(parts)


# Block types whose content is already captured via Title/Description
_SKIP_BLOCK_TYPES = {"title", "description"}

# Block types that are dynamic/runtime and have no static text
_DYNAMIC_BLOCK_TYPES = {"listing", "search", "querystringSortOn", "maps"}


def _extract_block_text(block: Dict[str, Any]) -> str:
    """Extract meaningful text from a single Volto block, skipping metadata."""
    if not isinstance(block, dict):
        return str(block) if isinstance(block, str) else ""

    block_type = (block.get("@type") or "").lower()

    if block_type in _SKIP_BLOCK_TYPES:
        return ""

    if block_type in _DYNAMIC_BLOCK_TYPES:
        return ""

    if block_type in ("slate", "text"):
        plaintext = block.get("plaintext")
        if isinstance(plaintext, str) and plaintext.strip():
            return strip_html(plaintext)
        value = block.get("value")
        if isinstance(value, list):
            parts: List[str] = []
            for node in value:
                if isinstance(node, dict):
                    node_text = node.get("text") or ""
                    if not node_text and node.get("children"):
                        node_text = _flatten_slate_children(node.get("children"))
                    node_text = strip_html(node_text.strip()) if node_text else ""
                    if node_text:
                        parts.append(node_text)
            if parts:
                return "\n".join(parts)
        elif isinstance(value, dict):
            children = value.get("children")
            if isinstance(children, list):
                text = _flatten_slate_children(children)
                if text.strip():
                    return strip_html(text)
        text_field = block.get("text")
        if isinstance(text_field, str) and text_field.strip():
            return strip_html(text_field)
        return ""

    if block_type == "image":
        label = block.get("alt") or block.get("caption") or block.get("title") or ""
        if isinstance(label, str) and label.strip():
            return f"[Image: {strip_html(label)}]"
        return ""

    if block_type == "video":
        label = block.get("title") or block.get("description") or ""
        if isinstance(label, str) and label.strip():
            return f"[Video: {strip_html(label)}]"
        return ""

    if block_type == "table":
        table_data = block.get("table") or {}
        rows = table_data.get("rows") or []
        if not rows:
            return ""
        row_texts: List[str] = []
        for row in rows:
            cells = row.get("cells") or []
            cell_texts: List[str] = []
            for cell in cells:
                cell_value = cell.get("value")
                if isinstance(cell_value, list):
                    cell_texts.append(_flatten_slate_children(cell_value).strip())
                elif isinstance(cell_value, str):
                    cell_texts.append(strip_html(cell_value))
                else:
                    cell_texts.append("")
            row_texts.append(" | ".join(cell_texts))
        return "\n".join(row_texts)

    if block_type == "teaser":
        parts: List[str] = []
        for key in ("head_title", "title", "description"):
            val = block.get(key)
            if isinstance(val, str) and val.strip():
                parts.append(strip_html(val))
        return " - ".join(parts) if parts else ""

    if block_type == "hero":
        parts: List[str] = []
        for key in ("title", "description"):
            val = block.get(key)
            if isinstance(val, str) and val.strip():
                parts.append(strip_html(val))
        return " - ".join(parts) if parts else ""

    if "quote" in block_type:
        parts: List[str] = []
        quote_text = block.get("quote") or block.get("text") or ""
        if isinstance(quote_text, str) and quote_text.strip():
            parts.append(strip_html(quote_text))
        attribution = (
            block.get("attribution")
            or block.get("source")
            or block.get("cite")
            or block.get("citation")
            or ""
        )
        if isinstance(attribution, str) and attribution.strip():
            parts.append(f"-- {strip_html(attribution)}")
        return " ".join(parts) if parts else ""

    if block_type in ("columnsblock", "columns"):
        columns = block.get("columns") or []
        col_parts: List[str] = []
        for col in columns:
            if isinstance(col, dict):
                col_text = _extract_from_blocks_layout(
                    col.get("blocks"), col.get("blocks_layout")
                )
                if col_text:
                    col_parts.append(col_text)
        return "\n\n".join(col_parts)

    if block_type in ("tabs", "accordion"):
        tabs = block.get("tabs") or block.get("data", {}).get("tabs") or []
        tab_parts: List[str] = []
        for tab in tabs:
            if isinstance(tab, dict):
                heading = tab.get("title") or ""
                body = _extract_from_blocks_layout(
                    tab.get("blocks"), tab.get("blocks_layout")
                )
                if heading and body:
                    tab_parts.append(f"{strip_html(heading)}: {body}")
                elif body:
                    tab_parts.append(body)
        return "\n\n".join(tab_parts)

    data = block.get("data")
    if isinstance(data, dict) and data.get("blocks") and data.get("blocks_layout"):
        return _extract_from_blocks_layout(data["blocks"], data["blocks_layout"])

    if block.get("blocks") and block.get("blocks_layout"):
        return _extract_from_blocks_layout(block["blocks"], block["blocks_layout"])

    for key in ("plaintext", "text", "description", "headline"):
        val = block.get(key)
        if isinstance(val, str) and val.strip():
            return strip_html(val)

    value = block.get("value")
    if isinstance(value, list):
        text = _flatten_slate_children(value)
        if text.strip():
            return strip_html(text)

    return ""


def _extract_from_blocks_layout(
    blocks: Optional[Dict[str, Any]],
    blocks_layout: Optional[Dict[str, Any]],
) -> str:
    """Extract text from blocks in layout order."""
    if not blocks:
        return ""
    if not isinstance(blocks, dict):
        try:
            blocks = dict(blocks)
        except (TypeError, ValueError):
            return ""
    ordered_ids: List[str] = []
    if blocks_layout and not isinstance(blocks_layout, dict):
        try:
            blocks_layout = dict(blocks_layout)
        except (TypeError, ValueError):
            blocks_layout = {}
    if isinstance(blocks_layout, dict):
        ordered_ids = blocks_layout.get("items") or []

    block_items: List[Dict[str, Any]] = []
    for bid in ordered_ids:
        if bid in blocks:
            block_items.append(blocks[bid])
    if not block_items:
        block_items = list(blocks.values())

    parts: List[str] = []
    for block in block_items:
        text = _extract_block_text(block)
        if text:
            parts.append(text)
    return "\n\n".join(parts)


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
        if isinstance(quote_text, str):
            cleaned_quote = strip_html(quote_text)
            if cleaned_quote:
                quotes.append(
                    {
                        "quote": cleaned_quote,
                        "attribution": strip_html(attribution) if isinstance(attribution, str) else "",
                    }
                )
        slate_value = block.get("value")
        if isinstance(slate_value, dict):
            data = slate_value.get("data") or {}
            if isinstance(data, dict):
                quote = data.get("text") or data.get("quote")
                cite = data.get("citation") or data.get("attribution") or data.get("source")
                if isinstance(quote, str):
                    cleaned_quote = strip_html(quote)
                    if cleaned_quote:
                        quotes.append(
                            {
                                "quote": cleaned_quote,
                                "attribution": strip_html(cite) if isinstance(cite, str) else "",
                            }
                        )
        if "children" in block:
            slate_text = _flatten_slate_children(block.get("children"))
            if slate_text and "quote" in (block.get("@type") or "").lower():
                quotes.append({"quote": strip_html(slate_text), "attribution": ""})
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


def extract_page_text(obj: Any) -> Tuple[str, List[Dict[str, str]]]:
    if obj is None:
        return "", []
    parts: List[str] = []

    title = getattr(obj, "Title", None)
    if callable(title):
        title_text = strip_html(str(title()))
    elif isinstance(title, str):
        title_text = strip_html(title)
    else:
        title_text = ""
    if title_text:
        parts.append(title_text)

    description = getattr(obj, "Description", None)
    if callable(description):
        desc_text = strip_html(str(description()))
    elif isinstance(description, str):
        desc_text = strip_html(description)
    else:
        desc_text = ""
    if desc_text:
        parts.append(desc_text)

    body = getattr(obj, "text", None)
    if callable(body):
        body = body()
    if isinstance(body, str):
        body_text = strip_html(body)
        if body_text:
            parts.append(body_text)
    elif hasattr(body, "output"):
        body_text = strip_html(str(body.output))
        if body_text:
            parts.append(body_text)

    blocks = getattr(obj, "blocks", None) or getattr(obj, "getBlocks", lambda: {})()
    blocks_layout = getattr(obj, "blocks_layout", None) or getattr(
        obj, "getBlocksLayout", lambda: {}
    )()

    if blocks and not isinstance(blocks, dict):
        try:
            blocks = dict(blocks)
        except (TypeError, ValueError):
            blocks = {}

    if isinstance(blocks, dict) and blocks:
        body_text = _extract_from_blocks_layout(blocks, blocks_layout)
        if not body_text:
            fallback_parts: List[str] = []
            for block in blocks.values():
                if not isinstance(block, dict):
                    continue
                btype = (block.get("@type") or "").lower()
                if btype in _SKIP_BLOCK_TYPES or btype in _DYNAMIC_BLOCK_TYPES:
                    continue
                raw = _flatten_block_value(block)
                cleaned = strip_html(raw)
                if cleaned and len(cleaned) > 10:
                    fallback_parts.append(cleaned)
            body_text = "\n\n".join(fallback_parts)
        if body_text:
            parts.append(body_text)

    text = "\n\n".join(filter(None, parts))
    text = _truncate(text, MAX_PAGE_TEXT)

    block_items: List[Any] = []
    if isinstance(blocks, dict):
        ordered_ids: List[str] = []
        if isinstance(blocks_layout, dict):
            ordered_ids = blocks_layout.get("items") or []
        for bid in ordered_ids:
            if bid in blocks:
                block_items.append(blocks[bid])
        if not block_items:
            block_items = list(blocks.values())

    quotes = _collect_quotes(block_items)

    seen_quotes: set = set()
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


def _clean_preserve_paragraphs(value: str) -> str:
    """Clean text while preserving paragraph breaks (\\n\\n)."""
    text = strip_html(value or "")
    text = HTML_TOKEN_RE.sub(" ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    lines = text.split("\n")
    cleaned = "\n".join(" ".join(line.split()) for line in lines)
    return cleaned.strip()


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


_METADATA_LABEL_RE = re.compile(
    r"^(?:Title|Type|Description):\s*.*$", re.MULTILINE
)


def _strip_metadata_labels(text: str) -> str:
    """Remove frontend metadata labels (Title:, Type:, Description:, ---) from page content."""
    text = _METADATA_LABEL_RE.sub("", text)
    text = re.sub(r"^---\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def build_context_documents(context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    mode = (context or {}).get("mode") or "page"
    page_info = (context or {}).get("page") or {}
    query = (context or {}).get("query") or ""
    selection_text = (context or {}).get("selection_text") or ""
    uploads = (context or {}).get("uploads") or []

    obj, resolved = resolve_content(page_info)

    frontend_page_content = (context or {}).get("page_content") or ""
    raw_page_text = ""
    page_quotes: List[Dict[str, str]] = []
    if isinstance(frontend_page_content, str) and frontend_page_content.strip():
        stripped = _strip_metadata_labels(frontend_page_content)
        if stripped.strip():
            raw_page_text = stripped
            blocks = getattr(obj, "blocks", None) or {}
            blocks_layout = getattr(obj, "blocks_layout", None) or {}
            if isinstance(blocks, dict):
                ordered_ids: List[str] = []
                if isinstance(blocks_layout, dict):
                    ordered_ids = blocks_layout.get("items") or []
                block_items = [blocks[bid] for bid in ordered_ids if bid in blocks]
                if not block_items:
                    block_items = list(blocks.values())
                page_quotes = _collect_quotes(block_items)

    if not raw_page_text.strip():
        raw_page_text, page_quotes = extract_page_text(obj)

    page_text = _clean_preserve_paragraphs(raw_page_text)
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

    upload_docs: List[Dict[str, Any]] = []
    if isinstance(uploads, list):
        for item in uploads:
            if not isinstance(item, dict):
                continue
            uid = item.get("file_id") or item.get("id")
            text = clean_text(item.get("text") or "")
            title = item.get("name") or item.get("filename") or "Upload"
            if uid:
                upload_docs.append(
                    _build_doc(
                        doc_id=uid,
                        title=title,
                        url="",
                        text=text,
                        doc_type="upload",
                        score=0.6,
                    )
                )

    site_docs = collect_site_documents(page_doc)
    documents = [page_doc] + upload_docs + site_docs + related_docs

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
        "upload_docs": upload_docs,
        "quotes": page_quotes,
    }
