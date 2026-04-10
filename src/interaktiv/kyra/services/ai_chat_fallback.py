import re
from typing import Any, Dict, List, Optional

from interaktiv.kyra import logger
from interaktiv.kyra.services.ai_context import clean_text
from interaktiv.kyra.services.ai_chat_context import (
    _build_citations,
    _build_smart_summary,
    _build_used_context,
    _filter_citations_by_response,
    _format_citation_snippet,
    _format_page_text,
    _format_upload_snippet,
    _missing_page_content_message,
)
from interaktiv.kyra.services.ai_chat_intent import _detect_upload_intent


def _build_fallback_message(context_docs: Dict[str, Any], last_query: str) -> str:
    page_doc = context_docs.get("page_doc") or {}
    title = page_doc.get("title") or "This page"
    mode = context_docs.get("mode") or "page"
    query = context_docs.get("query")
    formatted_text = _format_page_text(page_doc.get("text") or "")
    cleaned_query = clean_text(last_query or "")

    upload_docs = context_docs.get("upload_docs") or []

    if not formatted_text:
        return _missing_page_content_message()

    if _detect_upload_intent(last_query) and upload_docs:
        lines = ["**Uploaded files summary:**"]
        for doc in upload_docs[:2]:
            snippet = _format_upload_snippet(doc)
            lines.append("")
            lines.append(f"- **{doc.get('title')}**: {snippet}")
        return "\n".join(lines)

    if mode == "summarize":
        raw_text = page_doc.get("text") or ""
        logger.info("[KYRA SUMMARY] title=%s raw_text_len=%s raw_preview=%s", title, len(raw_text), raw_text[:100] if raw_text else "EMPTY")
        result = _build_smart_summary(title, raw_text)
        logger.info("[KYRA SUMMARY] result_len=%s result_preview=%s", len(result), result[:200])
        return result
    if mode in ("related", "search"):
        label = query or title
        verb = "related content" if mode == "related" else "search results"
        return (
            f"{verb.capitalize()} for **{label}** are not reachable right now. "
            f"In the meantime, here is what I can share from **{title}**:\n\n{formatted_text}"
        )
    return f"**{title}**\n\n{formatted_text}"


def _local_fallback_response(
    context_docs: Dict[str, Any], capabilities: Dict[str, Any], last_query: str
) -> Dict[str, Any]:
    page_doc = context_docs.get("page_doc") or {}
    citations = _build_citations(context_docs)
    summary_text = _build_fallback_message(context_docs, last_query)
    citations = _filter_citations_by_response(citations, summary_text, context_docs)
    logger.warning(
        "[KYRA AI LOCAL FALLBACK] page=%s summary_len=%s",
        page_doc.get("id"),
        len(summary_text),
    )
    return {
        "message": {"role": "assistant", "content": summary_text},
        "citations": citations,
        "capabilities": capabilities,
        "used_context": _build_used_context(context_docs),
    }


def _site_only_response(
    context_docs: Dict[str, Any], capabilities: Dict[str, Any], last_query: str
) -> Dict[str, Any]:
    page_doc = context_docs.get("page_doc") or {}
    page_title = page_doc.get("title") or "this page"
    cleaned_query = clean_text(last_query or "")
    text = (
        f"I can only answer using information available on this site. "
        f"I couldn't find details for '{cleaned_query}' here. "
        f"Try asking about {page_title} or provide a different search term."
    )
    citations = _build_citations(context_docs) if context_docs.get("mode") in ("summarize", "related", "search") else []
    citations = _filter_citations_by_response(citations, text, context_docs)
    return {
        "message": {"role": "assistant", "content": text},
        "citations": citations,
        "capabilities": capabilities,
        "used_context": _build_used_context(context_docs),
    }


def _answer_from_quotes(context_docs: Dict[str, Any], query: str) -> Optional[Dict[str, Any]]:
    quotes = context_docs.get("quotes") or []
    if not quotes:
        return None

    query_tokens = {t for t in re.split(r"[^a-z0-9äöüß]+", (query or "").lower()) if len(t) >= 3}
    if not query_tokens:
        query_tokens = set()

    best: Optional[Dict[str, str]] = None
    best_score = 0
    for item in quotes:
        quote_text = (item.get("quote") or "").lower()
        attribution = (item.get("attribution") or "").lower()
        tokens = set()
        tokens.update(re.split(r"[^a-z0-9äöüß]+", quote_text))
        tokens.update(re.split(r"[^a-z0-9äöüß]+", attribution))
        tokens = {t for t in tokens if len(t) >= 3}
        overlap = len(tokens.intersection(query_tokens)) if query_tokens else 0
        score = overlap
        if not query_tokens and quote_text:
            score = len(quote_text)
        if score > best_score:
            best_score = score
            best = item

    if not best:
        return None

    page_doc = context_docs.get("page_doc") or {}
    citations = []
    if page_doc:
        citations.append(
            {
                "source_id": page_doc.get("id"),
                "label": page_doc.get("title") or page_doc.get("url"),
                "url": page_doc.get("url") or "",
                "snippet": _format_citation_snippet(page_doc),
            }
        )

    content = best.get("quote")
    if best.get("attribution"):
        content = f'{best.get("quote")} — {best.get("attribution")}'

    return {
        "message": {"role": "assistant", "content": content},
        "citations": citations,
        "capabilities": {},
    }


def _answer_from_page_text(context_docs: Dict[str, Any], query: str) -> Optional[Dict[str, Any]]:
    page_doc = context_docs.get("page_doc") or {}
    text = (page_doc.get("text") or "").strip()
    if not text:
        return None
    lowered_query = (query or "").lower()
    lowered_text = text.lower()

    def _sentence_with(token: str) -> Optional[str]:
        sentences = re.split(r"(?<=[.!?])\s+", text)
        for sentence in sentences:
            if token.lower() in sentence.lower():
                return sentence.strip()
        return None

    def _phrase_snippet(phrase: str) -> Optional[str]:
        lowered_phrase = phrase.lower()
        if lowered_phrase not in lowered_text:
            return None
        idx = lowered_text.index(lowered_phrase)
        start = max(0, idx - 80)
        end = min(len(text), idx + len(phrase) + 80)
        snippet = text[start:end].strip()
        return snippet or None

    content = None
    tokens = [t for t in re.split(r"[^a-z0-9äöüß]+", lowered_query) if len(t) >= 3]
    for token in tokens:
        if token and token in lowered_text:
            content = _phrase_snippet(token) or _sentence_with(token)
            if content:
                break

    if not content:
        return None

    citations = []
    if page_doc:
        citations.append(
            {
                "source_id": page_doc.get("id"),
                "label": page_doc.get("title") or page_doc.get("url"),
                "url": page_doc.get("url") or "",
                "snippet": _format_citation_snippet(page_doc),
            }
        )

    return {
        "message": {"role": "assistant", "content": content},
        "citations": citations,
        "capabilities": {},
    }
