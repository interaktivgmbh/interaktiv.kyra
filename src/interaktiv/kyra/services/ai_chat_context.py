import re
from typing import Any, Dict, List, Optional

from interaktiv.kyra import logger
from interaktiv.kyra.services.ai_context import clean_text
from plone import api
from zope.annotation.interfaces import IAnnotations

from interaktiv.kyra.services.ai_chat_intent import (
    _detect_content_intent,
    _detect_summary_intent,
)


CHAT_PROMPT_CACHE_KEY = "interaktiv.kyra.ai_chat_prompt_id_v5"

MAX_DOC_MESSAGE_TEXT = 3000
CITATION_SNIPPET_LIMIT = 140
UPLOAD_SNIPPET_LIMIT = 8000


def _resolve_context_from_payload(data: Dict[str, Any]):
    context = data.get("context") or {}
    page = context.get("page") or {}
    uid = page.get("uid")
    url = page.get("url")

    if uid:
        obj = api.content.get(UID=uid)
        if obj is not None:
            return obj

    if url and isinstance(url, str):
        portal = api.portal.get()
        portal_url = portal.absolute_url()
        if url.startswith("http") and url.startswith(portal_url):
            url = url[len(portal_url):]
        if url.startswith("/"):
            return api.content.get(path=url.lstrip("/"))

    return None


def _build_chat_prompt_payload() -> Dict[str, Any]:
    return {
        "name": "Kyra Chat v5",
        "prompt": (
            "You are Kyra AI, a helpful and friendly assistant for this website.\n\n"
            "LANGUAGE RULE (HIGHEST PRIORITY — NEVER VIOLATE):\n"
            "First, detect the language of the user message below. "
            "Then write your ENTIRE response in THAT language. "
            "If the user writes in English, respond ONLY in English. "
            "If the user writes in German, respond ONLY in German. "
            "The page content language is IRRELEVANT — always match the USER's language.\n\n"
            "Response format — ALWAYS use Markdown:\n"
            "- Use **bold** for emphasis and key terms.\n"
            "- Use *italic* for subtle emphasis, ++underline++ for underline, ~~strikethrough~~ when useful.\n"
            "- Use headings (##, ###) to structure longer answers.\n"
            "- Use bullet lists (- item) and numbered lists (1. item) for enumerations.\n"
            "- Use GFM pipe tables (| col | col |\\n|---|---|\\n| a | b |) whenever you present tabular data, comparisons, or structured key/value information.\n"
            "- Use `inline code` for identifiers and fenced ```code blocks``` for code or commands.\n"
            "- Use > blockquotes for citations.\n"
            "- NEVER claim you cannot output Markdown, tables, or bold — the chat UI renders Markdown fully.\n\n"
            "Other rules:\n"
            "- For greetings and smalltalk, respond naturally and warmly (Markdown still allowed).\n"
            "- For questions about the page, use ONLY the provided content to answer — "
            "write a coherent answer in your own words, do NOT copy-paste raw content.\n"
            "- For general knowledge questions, answer from your own knowledge.\n"
            "- Never output raw HTML tags (<div>, <span>, …), metadata, navigation elements, or technical markup — use Markdown instead.\n\n"
            "User message:\n{{input}}"
        ),
        "categories": ["Chat"],
        "actionType": "replace",
        "metadata": {"categories": ["Chat"], "action": "replace"},
    }


def _create_chat_prompt(kyra) -> Optional[str]:
    created = kyra.prompts.create(_build_chat_prompt_payload())
    if isinstance(created, dict) and created.get("error"):
        return None
    new_id = created.get("id") or created.get("_id")
    if isinstance(new_id, str) and new_id.strip():
        _set_cached_prompt_id(new_id)
        return new_id
    return None


def _get_cached_prompt_id() -> Optional[str]:
    portal = api.portal.get()
    annotations = IAnnotations(portal)
    value = annotations.get(CHAT_PROMPT_CACHE_KEY)
    if isinstance(value, str) and value.strip():
        return value
    return None


def _set_cached_prompt_id(prompt_id: str) -> None:
    if not isinstance(prompt_id, str) or not prompt_id.strip():
        return
    portal = api.portal.get()
    annotations = IAnnotations(portal)
    annotations[CHAT_PROMPT_CACHE_KEY] = prompt_id


def _clear_cached_prompt_id() -> None:
    portal = api.portal.get()
    annotations = IAnnotations(portal)
    if CHAT_PROMPT_CACHE_KEY in annotations:
        del annotations[CHAT_PROMPT_CACHE_KEY]


def _ensure_chat_prompt_id(kyra) -> Optional[str]:
    cached = _get_cached_prompt_id()
    if cached:
        return cached
    return _create_chat_prompt(kyra)


def _apply_prompt_fallback(
    kyra,
    messages: List[Dict[str, Any]],
    data: Dict[str, Any],
) -> Dict[str, Any]:
    last_user = ""
    for message in reversed(messages):
        if message.get("role") == "user":
            last_user = message.get("content") or ""
            break

    params = data.get("params") or {}
    context = data.get("context") or {}
    selection_text = context.get("selection_text") or ""
    page_content = context.get("page_content") or ""

    if selection_text:
        return _apply_selection_prompt_fallback(
            kyra, last_user, selection_text, params
        )

    if page_content and (
        _detect_summary_intent(last_user) or _detect_content_intent(last_user)
    ):
        return _apply_page_context_prompt(kyra, last_user, page_content, params)

    apply_payload: Dict[str, Any] = {"query": last_user, "input": last_user}
    if isinstance(params, dict) and params.get("language"):
        apply_payload["language"] = params.get("language")

    prompt_id = _ensure_chat_prompt_id(kyra)
    if not prompt_id:
        return {"error": "Unable to create chat prompt"}

    response = kyra.prompts.apply(prompt_id, apply_payload)
    if isinstance(response, dict) and response.get("error"):
        from interaktiv.kyra.services.ai_chat_intent import (
            _is_invalid_uuid_error_response,
            _is_not_found_error,
        )

        if (
            _is_not_found_error(str(response.get("error")))
            or _is_invalid_uuid_error_response(response)
        ):
            _clear_cached_prompt_id()
            prompt_id = _ensure_chat_prompt_id(kyra)
            if prompt_id:
                response = kyra.prompts.apply(prompt_id, apply_payload)
    return response


def _apply_page_context_prompt(
    kyra,
    user_query: str,
    page_content: str,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """Create a temp prompt with page content in the template for page-related questions."""
    truncated_content = page_content[:3500]
    prompt_payload = {
        "name": "Kyra Page Context",
        "prompt": (
            "You are Kyra AI, a helpful assistant for this website. "
            "Answer the user's question based on the page content below. "
            "Write a natural, well-structured answer in your own words. "
            "Do NOT copy-paste the raw content. "
            "CRITICAL: Reply in the SAME language the user writes in. English question → English answer. German question → German answer. NEVER switch languages regardless of page content language.\n\n"
            "ALWAYS format the answer as Markdown: use **bold**, *italic*, ++underline++, "
            "headings (##), bullet lists, numbered lists, GFM pipe tables (| col | col | …), "
            "`inline code`, fenced ```code``` blocks and > blockquotes where appropriate. "
            "NEVER claim Markdown or tables are unsupported — the chat UI renders them fully.\n\n"
            f"Page content:\n{truncated_content}\n\n"
            "User question: {{input}}"
        ),
        "categories": ["Chat"],
        "actionType": "replace",
    }
    temp_id = None
    try:
        created = kyra.prompts.create(prompt_payload)
        if isinstance(created, dict) and created.get("error"):
            logger.warning("[KYRA PAGE CONTEXT PROMPT] create failed: %s", created.get("error"))
            return created
        temp_id = created.get("id") or created.get("_id")
        if not temp_id:
            return {"error": "Unable to create page context prompt"}

        apply_payload: Dict[str, Any] = {"query": user_query, "input": user_query}
        if isinstance(params, dict) and params.get("language"):
            apply_payload["language"] = params.get("language")

        response = kyra.prompts.apply(temp_id, apply_payload)
        logger.info("[KYRA PAGE CONTEXT PROMPT] applied temp=%s", temp_id)
        return response
    finally:
        if temp_id:
            try:
                kyra.prompts.delete(temp_id)
            except Exception:
                pass


def _apply_selection_prompt_fallback(
    kyra,
    user_query: str,
    source_text: str,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """Create a temp prompt for selection-based requests (translate, rewrite, etc.)."""
    prompt_payload = {
        "name": "Kyra Chat Selection",
        "prompt": (
            f"{user_query}\n\n"
            "Apply the above instruction to the following text. "
            "Return ONLY the resulting text, no explanations or metadata.\n\n"
            "{{input}}"
        ),
        "categories": ["Chat"],
        "actionType": "append",
    }
    temp_id = None
    try:
        created = kyra.prompts.create(prompt_payload)
        if isinstance(created, dict) and created.get("error"):
            logger.warning("[KYRA SELECTION PROMPT] create failed: %s", created.get("error"))
            return created
        temp_id = created.get("id") or created.get("_id")
        if not temp_id:
            return {"error": "Unable to create selection prompt"}

        apply_payload: Dict[str, Any] = {
            "query": source_text[:5000],
            "input": source_text[:5000],
        }
        if isinstance(params, dict) and params.get("language"):
            apply_payload["language"] = params.get("language")

        response = kyra.prompts.apply(temp_id, apply_payload)
        logger.info(
            "[KYRA SELECTION PROMPT] applied temp=%s result_keys=%s",
            temp_id,
            list(response.keys()) if isinstance(response, dict) else "N/A",
        )
        return response
    finally:
        if temp_id:
            try:
                kyra.prompts.delete(temp_id)
            except Exception:
                pass


def _build_system_message(context_docs: Dict[str, Any]) -> str:
    mode = context_docs.get("mode") or "page"
    documents = context_docs.get("documents") or []
    lines = [
        "You are Kyra AI, a knowledgeable and helpful assistant for this website.",
        "",
        "## #1 RULE — LANGUAGE (HIGHEST PRIORITY)",
        "Detect the language of the user's LAST message. Reply ONLY in that language.",
        "- User writes English → reply in English.",
        "- User writes German → reply in German.",
        "- The page content may be in a different language — IGNORE it for choosing your reply language.",
        "- This rule overrides everything else.",
        "",
        "## Response format",
        "- Write in well-structured, natural language. Use markdown formatting: **bold** for emphasis, headings (##), bullet points, and clear paragraphs.",
        "- NEVER output technical metadata, labels, or raw data such as 'Title:', 'Type:', 'Description:', '---', block types, UIDs, or internal field names.",
        "- When summarizing, write a coherent, readable summary covering all main topics. Organize into logical sections with headings if the content has multiple distinct topics.",
        "- Provide comprehensive answers of appropriate length — not too short, not excessively long.",
        "",
        "## Content rules",
        f"- Current mode: {mode}",
        "- You have access to context documents about the current page. Use them when the user asks about the page, its content, or related topics.",
        "- For general questions, greetings, or smalltalk, respond naturally from your own knowledge — do NOT repeat page content.",
        "- When you use information from a context document, mention its page title so the reader knows where it comes from.",
        "- If the user asks about the page and the answer cannot be found in the provided documents, clearly state that.",
        "- Do not invent facts about the page that are not present in the context.",
        "",
        "## Context Documents",
    ]
    for doc in documents[:6]:
        title = doc.get("title") or doc.get("url") or "Document"
        url = doc.get("url", "")
        doc_type = doc.get("type", "")
        snippet = (doc.get("text") or "")[:300].replace("\n", " ")
        lines.append(f"- [{doc_type}] {title} ({url}): {snippet}")

    selection_text = context_docs.get("selection_text") or ""
    if selection_text:
        lines.append("")
        lines.append("## User's Selected Text")
        lines.append(
            "The user has selected the following text on the page. "
            "Focus your response specifically on this text. "
            "When asked to translate, rewrite, or transform, apply it to this text only:"
        )
        lines.append(selection_text[:3000])

    return "\n".join(lines)


def _format_context_doc_message(doc: Dict[str, Any]) -> Dict[str, str]:
    content = f"Document: {doc.get('title')} ({doc.get('url')})\n\n{doc.get('text') or ''}"
    if len(content) > MAX_DOC_MESSAGE_TEXT:
        content = content[:MAX_DOC_MESSAGE_TEXT].rsplit(" ", 1)[0] + "..."
    return {"role": "tool", "content": content}


def _build_citations(context_docs: Dict[str, Any]) -> List[Dict[str, Any]]:
    page_doc = context_docs.get("page_doc") or {}
    site_docs = context_docs.get("site_docs") or []
    related_docs = context_docs.get("related_docs") or []
    upload_docs = context_docs.get("upload_docs") or []
    citation_candidates: List[Dict[str, Any]] = []

    if page_doc:
        citation_candidates.append(page_doc)
    citation_candidates.extend(upload_docs)
    citation_candidates.extend(related_docs or [])
    citation_candidates.extend(site_docs or [])

    citations: List[Dict[str, Any]] = []
    seen = set()
    for doc in citation_candidates:
        if not doc:
            continue
        source_id = doc.get("id") or doc.get("url")
        if not source_id or source_id in seen:
            continue
        seen.add(source_id)
        snippet = _format_citation_snippet(doc)
        label = doc.get("title") or doc.get("url") or "Document"
        citations.append(
            {
                "source_id": source_id,
                "label": label,
                "url": doc.get("url") or "",
                "snippet": snippet,
            }
        )
        if len(citations) >= 5:
            break
    return citations


def _filter_citations_by_response(
    citations: List[Dict[str, Any]],
    response_text: str,
    context_docs: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Keep only citations for sources actually referenced in the response."""
    if not citations or not response_text:
        return citations

    mode = context_docs.get("mode") or "page"
    page_doc = context_docs.get("page_doc") or {}
    page_id = page_doc.get("id") or page_doc.get("url")
    upload_ids = {
        doc.get("id") or doc.get("url")
        for doc in (context_docs.get("upload_docs") or [])
        if doc.get("id") or doc.get("url")
    }

    response_lower = response_text.lower()
    filtered = []

    for citation in citations:
        source_id = citation.get("source_id")
        label = (citation.get("label") or "").strip()
        url = citation.get("url") or ""

        # Always keep the current page in page/summarize modes
        if source_id == page_id and mode in ("page", "summarize"):
            filtered.append(citation)
            continue

        # Always keep uploads (user explicitly provided these)
        if source_id in upload_ids:
            filtered.append(citation)
            continue

        # Keep if title is mentioned in response (min 3 chars to avoid false positives)
        if label and len(label) >= 3 and label.lower() in response_lower:
            filtered.append(citation)
            continue

        # Keep if URL is referenced in response
        if url and url in response_text:
            filtered.append(citation)
            continue

    # If filtering removed everything but we had citations, keep at least the page doc
    if not filtered and citations and page_id:
        for citation in citations:
            if citation.get("source_id") == page_id:
                filtered.append(citation)
                break

    return filtered


def _build_used_context(context_docs: Dict[str, Any]) -> List[Dict[str, Any]]:
    documents = context_docs.get("documents") or []
    return [
        {
            "id": doc.get("id"),
            "title": doc.get("title"),
            "url": doc.get("url"),
            "type": doc.get("type"),
            "score": doc.get("score"),
        }
        for doc in documents
    ]


def _missing_page_content_message() -> str:
    return (
        "I can't access this page's content yet. Please check permissions or try again later."
    )


def _build_smart_summary(title: str, raw_text: str) -> str:
    """Build a structured summary from raw page text."""
    if not raw_text or not raw_text.strip():
        return f"Die Seite **{title}** enthält aktuell keine Textinhalte."

    text = raw_text.strip()
    paragraphs = re.split(r"\n\s*\n|\n(?=[A-ZÄÖÜ])", text)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]
    paragraphs = [
        p for p in paragraphs
        if len(p) > 20
        and not p.startswith("[Image")
        and not p.startswith("Title:")
        and not p.startswith("Type:")
        and p != "---"
    ]

    if not paragraphs:
        return f"Die Seite **{title}** enthält aktuell keine Textinhalte."

    parts = [f"**{title}**\n"]

    if len(paragraphs) <= 3:
        for p in paragraphs:
            parts.append(p)
    else:
        parts.append(paragraphs[0])
        parts.append("")
        parts.append("**Weitere Themen auf dieser Seite:**")
        for p in paragraphs[1:8]:
            sentence = re.split(r"(?<=[.!?])\s", p, maxsplit=1)[0]
            if len(sentence) > 150:
                sentence = sentence[:147] + "..."
            parts.append(f"- {sentence}")

        remaining = len(paragraphs) - 8
        if remaining > 0:
            parts.append(f"\n*...und {remaining} weitere Abschnitte.*")

    return "\n".join(parts)


def _format_page_text(text: str, max_chars: int = 1500) -> str:
    """Format extracted page text for display as a fallback summary."""
    if not text or not text.strip():
        return ""

    result = text.strip()
    if len(result) > max_chars:
        result = result[:max_chars].rsplit(" ", 1)[0] + "..."

    result = re.sub(
        r"(?<=\S)\s+(\d{1,2})\.\s+([A-ZÄÖÜ])",
        r"\n\n**\1. \2",
        result,
    )

    result = re.sub(
        r"^(\d{1,2})\.\s+([A-ZÄÖÜ])",
        r"**\1. \2",
        result,
        flags=re.MULTILINE,
    )

    lines = result.split("\n")
    formatted_lines: list = []
    for line in lines:
        if line.startswith("**") and "**" not in line[2:]:
            m = re.match(
                r"(\*\*\d{1,2}\.\s+\S+(?:\s+\S+){0,5}?)\s+((?:In|Im|Der|Die|Das|Es|Ein|Eine|Neben|Sofern|Sie|Wenn|Wir|Für|Bei|Auf|Unter|Über|Nach|Vor|Durch|Zum|Zur|Mit|Hier|The|A|An|If|For|When|This|It|You|We|To)\s)",
                line,
            )
            if m:
                formatted_lines.append(f"{m.group(1)}**\n{m.group(2)}{line[m.end():]}")
            else:
                formatted_lines.append(f"{line}**")
        else:
            formatted_lines.append(line)

    return "\n".join(formatted_lines)


def _format_citation_snippet(doc: Dict[str, Any]) -> str:
    snippet = clean_text(doc.get("text") or "")
    if not snippet:
        snippet = clean_text(doc.get("title") or doc.get("url") or "")
    snippet = snippet.strip()
    if len(snippet) > CITATION_SNIPPET_LIMIT:
        snippet = snippet[:CITATION_SNIPPET_LIMIT].rsplit(" ", 1)[0] + "..."
    return snippet


def _format_upload_snippet(doc: Dict[str, Any]) -> str:
    """Return upload text without aggressive ellipsis."""
    snippet = clean_text(doc.get("text") or "")
    if not snippet:
        snippet = clean_text(doc.get("title") or doc.get("url") or "")
    snippet = snippet.strip()
    if len(snippet) > UPLOAD_SNIPPET_LIMIT:
        snippet = snippet[:UPLOAD_SNIPPET_LIMIT]
    return snippet
