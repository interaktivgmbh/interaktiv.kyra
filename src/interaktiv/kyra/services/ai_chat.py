import json
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from interaktiv.kyra.services.base import ServiceBase
from interaktiv.kyra import logger
from interaktiv.kyra.services.ai_capabilities import _capabilities_for
from interaktiv.kyra.services.ai_context import build_context_documents, clean_text
from interaktiv.kyra.services.ai_chat_upload import _get_uploads_store
from plone import api
from plone.restapi.deserializer import json_body
from zExceptions import BadRequest
from zope.interface import implementer
from zope.annotation.interfaces import IAnnotations
from zope.publisher.interfaces import IPublishTraverse


def _validate_messages(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    messages = data.get("messages")
    if not isinstance(messages, list) or not messages:
        raise BadRequest("Missing 'messages' array")

    normalized = []
    for message in messages:
        if not isinstance(message, dict):
            raise BadRequest("Each message must be an object")
        role = message.get("role")
        content = message.get("content")
        if role not in ("user", "assistant", "system", "tool"):
            raise BadRequest("Invalid message role")
        if not isinstance(content, str):
            raise BadRequest("Message content must be a string")
        normalized.append({"role": role, "content": content})
    return normalized


def _build_gateway_payload(data: Dict[str, Any], messages: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], str]:
    payload: Dict[str, Any] = {"messages": messages}
    if data.get("conversation_id"):
        payload["conversation_id"] = data.get("conversation_id")
    if data.get("context") is not None:
        payload["context"] = data.get("context")
    if data.get("params") is not None:
        payload["params"] = data.get("params")

    last_user = ""
    for message in reversed(messages):
        if message.get("role") == "user":
            last_user = message.get("content") or ""
            break
    if last_user:
        payload.setdefault("query", last_user)
        payload.setdefault("input", last_user)
    return payload, last_user


def _extract_assistant_text(data: Any) -> str:
    if isinstance(data, dict):
        message = data.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                return content

        for key in ("response", "result", "content", "text", "output"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value

    if isinstance(data, str):
        return data

    return ""


def _extract_citations(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, dict):
        citations = data.get("citations") or data.get("sources")
        if isinstance(citations, list):
            return citations
    return []


def _extract_conversation_id(data: Any, fallback: Optional[str]) -> Optional[str]:
    if isinstance(data, dict):
        return data.get("conversation_id") or data.get("conversationId") or fallback
    return fallback


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


CHAT_PROMPT_CACHE_KEY = "interaktiv.kyra.ai_chat_prompt_id_v4"


def _build_chat_prompt_payload() -> Dict[str, Any]:
    return {
        "name": "Kyra Chat v4",
        "prompt": (
            "You are Kyra AI, a helpful and friendly assistant for this website.\n\n"
            "LANGUAGE RULE (HIGHEST PRIORITY — NEVER VIOLATE):\n"
            "First, detect the language of the user message below. "
            "Then write your ENTIRE response in THAT language. "
            "If the user writes in English, respond ONLY in English. "
            "If the user writes in German, respond ONLY in German. "
            "The page content language is IRRELEVANT — always match the USER's language.\n\n"
            "Other rules:\n"
            "- For greetings and smalltalk, respond naturally and warmly.\n"
            "- For questions about the page, use ONLY the provided content to answer — "
            "write a coherent answer in your own words, do NOT copy-paste raw content.\n"
            "- For general knowledge questions, answer from your own knowledge.\n"
            "- Never output raw HTML, metadata, navigation elements, or technical markup.\n\n"
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

    # Selection text: use dedicated temp prompt for text operations
    if selection_text:
        return _apply_selection_prompt_fallback(
            kyra, last_user, selection_text, params
        )

    # For page-related questions, create a temp prompt with page content baked
    # into the template (not the input) — the gateway ignores long input values
    # but respects the prompt template content.
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
    # The gateway replaces {{input}} with the apply_payload "input" value.
    # Embed the user instruction in the prompt template, pass the text as input.
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


MAX_DOC_MESSAGE_TEXT = 3000
CITATION_SNIPPET_LIMIT = 140
UPLOAD_SNIPPET_LIMIT = 8000


def _is_not_found_error(message: str) -> bool:
    lowered = (message or "").lower()
    return "404" in lowered or "not found" in lowered


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
        content = content[:MAX_DOC_MESSAGE_TEXT].rsplit(" ", 1)[0] + "…"
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

    # Clean and split into meaningful paragraphs
    text = raw_text.strip()
    # Split on double newlines, single newlines with enough content, or sentence boundaries
    paragraphs = re.split(r"\n\s*\n|\n(?=[A-ZÄÖÜ])", text)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]
    # Filter out very short fragments, image tags, metadata
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
        # First paragraph as intro
        parts.append(paragraphs[0])
        parts.append("")
        parts.append("**Weitere Themen auf dieser Seite:**")
        for p in paragraphs[1:8]:
            # Take first sentence as summary
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

    # Break numbered headings onto their own line when inline
    # e.g. "...erhalten. 01. Voraussetzungen In der Regel..." →
    #      "...erhalten.\n\n**01. Voraussetzungen**\nIn der Regel..."
    result = re.sub(
        r"(?<=\S)\s+(\d{1,2})\.\s+([A-ZÄÖÜ])",
        r"\n\n**\1. \2",
        result,
    )

    # Bold numbered headings already at start of a line
    result = re.sub(
        r"^(\d{1,2})\.\s+([A-ZÄÖÜ])",
        r"**\1. \2",
        result,
        flags=re.MULTILINE,
    )

    # Close bold markers: find end of heading (next sentence or next paragraph)
    lines = result.split("\n")
    formatted_lines: list = []
    for line in lines:
        if line.startswith("**") and "**" not in line[2:]:
            # Line has unclosed bold — close it at end of the heading phrase
            # Heading ends before the first sentence-continuation pattern
            m = re.match(
                r"(\*\*\d{1,2}\.\s+\S+(?:\s+\S+){0,5}?)\s+((?:In|Im|Der|Die|Das|Es|Ein|Eine|Neben|Sofern|Sie|Wenn|Wir|Für|Bei|Auf|Unter|Über|Nach|Vor|Durch|Zum|Zur|Mit|Hier|The|A|An|If|For|When|This|It|You|We|To)\s)",
                line,
            )
            if m:
                formatted_lines.append(f"{m.group(1)}**\n{m.group(2)}{line[m.end():]}")
            else:
                # Close at end of line
                formatted_lines.append(f"{line}**")
        else:
            formatted_lines.append(line)

    return "\n".join(formatted_lines)


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
    if cleaned_query:
        return f"**{title}**\n\n{formatted_text}"
    return f"**{title}**\n\n{formatted_text}"


def _format_citation_snippet(doc: Dict[str, Any]) -> str:
    snippet = clean_text(doc.get("text") or "")
    if not snippet:
        snippet = clean_text(doc.get("title") or doc.get("url") or "")
    snippet = snippet.strip()
    if len(snippet) > CITATION_SNIPPET_LIMIT:
        snippet = snippet[:CITATION_SNIPPET_LIMIT].rsplit(" ", 1)[0] + "…"
    return snippet


def _format_upload_snippet(doc: Dict[str, Any]) -> str:
    """Return upload text without the aggressive ellipsis we use for citations."""
    snippet = clean_text(doc.get("text") or "")
    if not snippet:
        snippet = clean_text(doc.get("title") or doc.get("url") or "")
    snippet = snippet.strip()
    if len(snippet) > UPLOAD_SNIPPET_LIMIT:
        # Keep it long but avoid the trailing ellipsis that confused users
        snippet = snippet[:UPLOAD_SNIPPET_LIMIT]
    return snippet


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
        f"I couldn’t find details for '{cleaned_query}' here. "
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


def _is_invalid_uuid_error_response(response: Any) -> bool:
    if not isinstance(response, dict):
        return False

    details = response.get("details") or []
    if isinstance(details, list):
        for detail in details:
            if not isinstance(detail, dict):
                continue
            message = detail.get("message") or ""
            if isinstance(message, str) and "invalid uuid" in message.lower():
                return True

    message = response.get("error") or response.get("message") or ""
    if isinstance(message, str) and "invalid uuid" in message.lower():
        return True

    return False


def _is_unusable_gateway_answer(text: str) -> bool:
    if not text:
        return True
    lowered = text.lower()
    if "please modify the text according to the instruction" in lowered:
        return True
    if "modifique el texto de acuerdo con las instrucciones" in lowered:
        return True
    if "modifiez le texte selon les instructions" in lowered:
        return True
    if "tinymce" in lowered:
        return True
    if "maintaining proper tinymce html formatting" in lowered:
        return True
    if lowered.strip() in ("please summarize the content of this page.",
                           "please summarize the page content clearly and concisely."):
        return True
    if "please summarize" in lowered and "page" in lowered:
        return True
    if "bitte" in lowered and "fassen" in lowered and "zusammen" in lowered:
        return True
    if "bitte fassen sie den inhalt zusammen" in lowered:
        return True
    if "please use the search bar" in lowered:
        return True
    if "please enter your search" in lowered or "search box" in lowered or "enter your search terms" in lowered:
        return True
    if "please enter your query" in lowered and "search" in lowered:
        return True
    if "cannot find" in lowered and "content" in lowered and "provided" in lowered:
        return True
    return False


def _is_grounded_answer(text: str, context_docs: Dict[str, Any]) -> bool:
    """Heuristic: answer should reference the current page title/URL or page text."""
    if not text:
        return False
    page_doc = context_docs.get("page_doc") or {}
    title = (page_doc.get("title") or "").strip()
    url = (page_doc.get("url") or "").strip()
    lowered = text.lower()
    if title and title.lower() in lowered:
        return True
    if url and url.lower() in lowered:
        return True

    page_text = (page_doc.get("text") or "").lower()
    if page_text:
        import re

        answer_tokens = {t for t in re.split(r"[^a-z0-9äöüß]+", lowered) if len(t) >= 4}
        page_tokens = {
            t for t in re.split(r"[^a-z0-9äöüß]+", page_text[:800]) if len(t) >= 4
        }
        if answer_tokens and page_tokens:
            if len(answer_tokens.intersection(page_tokens)) >= 3:
                return True
    return False


SUMMARY_KEYWORDS = (
    "summarize",
    "summary",
    "zusammenfassen",
    "zusammenfassung",
    "fasse",
    "zusammen",
    "wesentlichen informationen",
    "wesentliche informationen",
)

SITE_TITLE_KEYWORDS = (
    "site title",
    "site name",
    "website title",
    "webseitentitel",
    "haupttitel der website",
    "main seiten titel",
    "seitentitel der website",
)
PAGE_TITLE_KEYWORDS = ("page title", "titel der seite", "seitentitel", "seiten titel")

SMALLTALK_KEYWORDS = (
    "hallo",
    "hi",
    "hey",
    "wie geht",
    "hello",
    "was geht",
    "servus",
    "moin",
    "grüß",
    "gruss",
    "was kannst du",
    "what can you do",
    "wer bist du",
    "who are you",
    "hilfe",
    "help",
    "guten tag",
    "guten morgen",
    "guten abend",
    "good morning",
    "good evening",
    "danke",
    "thank",
)

OFFSITE_KEYWORDS = (
    "wetter",
    "weather",
    "forecast",
    "temperature",
    "temperatur",
    "new york",
    "paris",
    "london",
    "berlin",
    "time",
    "uhrzeit",
    "stock",
    "aktien",
    "news",
)


def _detect_summary_intent(text: str) -> bool:
    lowered = (text or "").lower()
    return any(keyword in lowered for keyword in SUMMARY_KEYWORDS)


def _detect_smalltalk_intent(text: str) -> bool:
    lowered = (text or "").lower().strip().rstrip("?!.,")
    if not lowered:
        return False
    if len(lowered) > 80:
        return False
    return any(keyword in lowered for keyword in SMALLTALK_KEYWORDS)


def _detect_offsite_intent(text: str) -> bool:
    lowered = (text or "").lower()
    if not lowered:
        return False
    return any(keyword in lowered for keyword in OFFSITE_KEYWORDS)


CONTENT_KEYWORDS = (
    "auf der seite",
    "auf dieser seite",
    "inhalt",
    "content",
    "quote",
    "zitat",
    "stay hungry",
    "stay foolish",
    "steve jobs",
    "wer hat",
    "who said",
    "welches zitat",
    "welches quote",
    "seitentitel",
    "page title",
    "titel der seite",
    "titel der webseite",
    "title of the page",
    "title of this page",
    "was steht",
    "what does the page",
    "worum geht",
    "what is this page about",
    "der titel",
    "the title",
    "welcher titel",
    "which title",
)

UPLOAD_KEYWORDS = (
    "attachment",
    "anhang",
    "upload",
    "hochgeladen",
    "datei",
    "file",
)


def _detect_content_intent(text: str) -> bool:
    lowered = (text or "").lower()
    return any(keyword in lowered for keyword in CONTENT_KEYWORDS)


def _detect_upload_intent(text: str) -> bool:
    lowered = (text or "").lower()
    return any(keyword in lowered for keyword in UPLOAD_KEYWORDS)


def _answer_from_quotes(context_docs: Dict[str, Any], query: str) -> Optional[Dict[str, Any]]:
    quotes = context_docs.get("quotes") or []
    if not quotes:
        return None

    import re

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
        import re

        sentences = re.split(r"(?<=[.!?])\s+", text)
        for sentence in sentences:
            if token.lower() in sentence.lower():
                return sentence.strip()
        return None

    def _phrase_snippet(phrase: str) -> Optional[str]:
        lowered_text = text.lower()
        lowered_phrase = phrase.lower()
        if lowered_phrase not in lowered_text:
            return None
        idx = lowered_text.index(lowered_phrase)
        start = max(0, idx - 80)
        end = min(len(text), idx + len(phrase) + 80)
        snippet = text[start:end].strip()
        return snippet or None

    content = None
    # try to find any meaningful token from the query inside the page text
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


def _detect_site_title_intent(text: str) -> bool:
    lowered = (text or "").lower()
    return any(keyword in lowered for keyword in SITE_TITLE_KEYWORDS)


def _detect_page_title_intent(text: str) -> bool:
    lowered = (text or "").lower()
    return any(keyword in lowered for keyword in PAGE_TITLE_KEYWORDS)


def _needs_grounded_response(last_query: str, mode: str, context_docs: Dict[str, Any]) -> bool:
    if mode == "summarize":
        return True
    if mode in ("search", "related"):
        return True
    if mode == "page":
        if _detect_smalltalk_intent(last_query):
            return False
        if _detect_content_intent(last_query):
            return True
        return False
    return False


def _sse_event(event: str, payload: Any) -> str:
    if isinstance(payload, str):
        data = payload
    else:
        data = json.dumps(payload)
    return f"event: {event}\ndata: {data}\n\n"


def _chunk_text(text: str, size: int = 32) -> Iterable[str]:
    if not text:
        return []
    return [text[i: i + size] for i in range(0, len(text), size)]


def _parse_gateway_stream_payload(
    event_type: str, data_text: str
) -> Tuple[str, Any]:
    payload: Any = data_text
    try:
        payload = json.loads(data_text)
    except Exception:
        payload = data_text

    if not event_type and isinstance(payload, dict):
        event_type = payload.get("type") or payload.get("event") or ""

    return event_type or "token", payload


@implementer(IPublishTraverse)
class AIChatService(ServiceBase):
    """POST /++api++/@ai-chat and /++api++/@ai-chat/stream"""

    def __init__(self, context, request):
        super().__init__(context, request)
        self.subpath = None

    def publishTraverse(self, request, name):
        if self.subpath is None:
            self.subpath = name
            return self
        raise BadRequest("Too many path segments")

    def __call__(self):
        accept = (self.request.getHeader("Accept") or "").lower()
        wants_stream = "text/event-stream" in accept

        if self.subpath == "stream" or wants_stream:
            return self._stream_response()
        if self.subpath:
            raise BadRequest("Unknown subpath")
        return super().__call__()

    def _prepare_gateway_payload(
        self, data: Dict[str, Any]
    ) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any], str, List[Dict[str, Any]]]:
        messages = _validate_messages(data)
        # detect intent from last user message
        last_user = ""
        for message in reversed(messages):
            if message.get("role") == "user":
                last_user = message.get("content") or ""
                break

        context_payload = data.get("context") or {}
        context_mode = context_payload.get("mode") or "page"
        if context_mode != "summarize" and _detect_summary_intent(last_user):
            context_payload = dict(context_payload)
            context_payload["mode"] = "summarize"
            context_mode = "summarize"

        # resolve uploads with stored extracted text
        uploads_raw = context_payload.get("uploads") or []
        resolved_uploads = []
        if isinstance(uploads_raw, list):
            store = _get_uploads_store()
            for item in uploads_raw:
                if not isinstance(item, dict):
                    continue
                file_id = item.get("file_id") or item.get("id")
                if not file_id:
                    continue
                store_item = store.get(file_id) if isinstance(store, dict) else None
                resolved_uploads.append(
                    {
                        "file_id": file_id,
                        "name": item.get("name") or (store_item or {}).get("filename"),
                        "text": (store_item or {}).get("extracted_text") or item.get("text"),
                    }
                )
        if resolved_uploads:
            context_payload["uploads"] = resolved_uploads

        context_docs = build_context_documents(context_payload)
        context_docs["mode"] = context_mode
        page_text = context_docs.get("page_doc", {}).get("text", "")
        if not page_text:
            return None, context_docs, "", messages

        system_message = _build_system_message(context_docs)
        documents = context_docs.get("documents") or []
        doc_messages = [
            _format_context_doc_message(doc)
            for doc in documents[:6]
            if doc.get("text")
        ]
        gateway_messages = [{"role": "system", "content": system_message}] + doc_messages + messages
        payload, last_user = _build_gateway_payload(data, gateway_messages)
        payload["context_documents"] = context_docs.get("documents", [])
        payload["documents"] = context_docs.get("documents", [])
        logger.debug(
            "[KYRA AI DOCS] count=%s payload=%s",
            len(doc_messages),
            [doc.get("title") for doc in context_docs.get("documents") or []][:3],
        )
        logger.info(
            "[KYRA AI PAYLOAD] mode=%s docs=%s page_id=%s",
            context_docs.get("mode"),
            len(context_docs.get("documents") or []),
            context_docs.get("page_doc", {}).get("id"),
        )
        return payload, context_docs, last_user, messages

    def reply(self):
        data = json_body(self.request) or {}
        if not isinstance(data, dict):
            raise BadRequest("JSON object expected")

        resolved_context = _resolve_context_from_payload(data)
        capabilities = _capabilities_for(resolved_context)
        payload, context_docs, last_query, messages = self._prepare_gateway_payload(data)
        last_query = last_query or ""

        # Quick local answers from quotes for content intent
        if _detect_content_intent(last_query):
            quote_answer = _answer_from_quotes(context_docs, last_query)
            if quote_answer:
                quote_answer["capabilities"] = capabilities
                quote_answer["used_context"] = _build_used_context(context_docs)
                return quote_answer
            text_answer = _answer_from_page_text(context_docs, last_query)
            if text_answer:
                text_answer["capabilities"] = capabilities
                text_answer["used_context"] = _build_used_context(context_docs)
                return text_answer
        if _detect_upload_intent(last_query) and context_docs.get("upload_docs"):
            upload_docs = context_docs.get("upload_docs") or []
            # Use markdown-style bold so it renders in plain-text UIs too
            lines = ["Uploaded files:", ""]
            for doc in upload_docs[:3]:
                snippet = _format_upload_snippet(doc)
                title = doc.get("title") or "Attachment"
                # Show filename on its own line, then the extracted text on the next line
                lines.append(f"- {title}:")
                lines.append(snippet)
                lines.append("")
            upload_content = "\n".join(lines)
            upload_citations = _filter_citations_by_response(
                _build_citations(context_docs), upload_content, context_docs
            )
            return {
                "message": {"role": "assistant", "content": upload_content},
                "citations": upload_citations,
                "capabilities": capabilities,
                "used_context": _build_used_context(context_docs),
            }

        # Block obvious off-site queries early
        if _detect_offsite_intent(last_query):
            return _site_only_response(context_docs, capabilities, last_query)

        # Quick intent handlers (no external call)
        if _detect_site_title_intent(last_query):
            portal = api.portal.get()
            site_title = getattr(portal, "Title", lambda: "this site")()
            site_doc = {}
            for doc in context_docs.get("site_docs") or []:
                if doc.get("type") == "site":
                    site_doc = doc
                    break
            citations = []
            if site_doc:
                citations.append(
                    {
                        "source_id": site_doc.get("id"),
                        "label": site_doc.get("title"),
                        "url": site_doc.get("url"),
                        "snippet": _format_citation_snippet(site_doc),
                    }
                )
            return {
                "message": {"role": "assistant", "content": f"The site title is: {site_title}"},
                "citations": citations,
                "capabilities": capabilities,
                "used_context": _build_used_context(context_docs),
            }

        if _detect_page_title_intent(last_query):
            page_doc = context_docs.get("page_doc") or {}
            page_title = page_doc.get("title") or "this page"
            citations = []
            if page_doc:
                citations.append(
                    {
                        "source_id": page_doc.get("id"),
                        "label": page_title,
                        "url": page_doc.get("url"),
                        "snippet": _format_citation_snippet(page_doc),
                    }
                )
            return {
                "message": {"role": "assistant", "content": f"The page title is: {page_title}"},
                "citations": citations,
                "capabilities": capabilities,
                "used_context": _build_used_context(context_docs),
            }

        logger.info(
            "[KYRA AI CONTEXT] mode=%s resolved=%s text_len=%s related=%s",
            context_docs.get("mode"),
            context_docs.get("resolved"),
            context_docs.get("page_text_length"),
            len(context_docs.get("related_docs") or []),
        )

        if not payload:
            return {
                "message": {
                    "role": "assistant",
                    "content": _missing_page_content_message(),
                },
                "citations": [],
                "capabilities": capabilities,
                "used_context": _build_used_context(context_docs),
            }

        messages_with_context = payload.get("messages", [])
        gateway_data = self.kyra.chat.send(payload)
        logger.info("[KYRA AI GATEWAY RESPONSE] type=%s keys=%s", type(gateway_data).__name__, list(gateway_data.keys()) if isinstance(gateway_data, dict) else "N/A")

        if isinstance(gateway_data, dict) and gateway_data.get("error"):
            logger.info("[KYRA AI GATEWAY ERROR BRANCH] error=%s", gateway_data.get("error"))
            prompt_response = _apply_prompt_fallback(self.kyra, messages_with_context, data)
            if isinstance(prompt_response, dict) and not prompt_response.get("error"):
                gateway_data = prompt_response
            else:
                error_message = gateway_data.get("error")
                if _is_not_found_error(str(error_message)):
                    return _local_fallback_response(context_docs, capabilities, last_query)
                logger.error("[KYRA AI GATEWAY ERROR] %s", error_message)
                raise BadRequest(error_message)

        assistant_text = _extract_assistant_text(gateway_data)
        logger.info("[KYRA AI EXTRACTED TEXT] len=%s preview=%s", len(assistant_text), assistant_text[:120] if assistant_text else "EMPTY")
        if not assistant_text:
            prompt_response = _apply_prompt_fallback(self.kyra, messages_with_context, data)
            if isinstance(prompt_response, dict) and not prompt_response.get("error"):
                gateway_data = prompt_response
                assistant_text = _extract_assistant_text(gateway_data)

        mode = context_docs.get("mode") or "page"
        selection_text = context_docs.get("selection_text") or ""
        needs_grounding = _needs_grounded_response(last_query, mode, context_docs)
        smalltalk = _detect_smalltalk_intent(last_query)

        # Skip grounding check for smalltalk, selection, and summary responses
        if smalltalk:
            needs_grounding = False
        if selection_text:
            needs_grounding = False
        if _detect_summary_intent(last_query) and assistant_text and not _is_unusable_gateway_answer(assistant_text):
            needs_grounding = False

        logger.info("[KYRA AI GROUNDING] needs=%s unusable=%s has_selection=%s", needs_grounding, _is_unusable_gateway_answer(assistant_text) if assistant_text else "N/A", bool(selection_text))

        if (
            not assistant_text
            or _is_unusable_gateway_answer(assistant_text)
            or (needs_grounding and not _is_grounded_answer(assistant_text, context_docs))
        ):
            if needs_grounding and mode in ("search", "related"):
                return _local_fallback_response(context_docs, capabilities, last_query)
            if needs_grounding and _detect_summary_intent(last_query):
                return _local_fallback_response(context_docs, capabilities, last_query)
            if needs_grounding:
                return _site_only_response(context_docs, capabilities, last_query)
            return _local_fallback_response(context_docs, capabilities, last_query)

        conversation_id = _extract_conversation_id(
            gateway_data, data.get("conversation_id")
        )

        gateway_citations = []
        if isinstance(gateway_data, dict):
            gateway_citations = gateway_data.get("citations") or []
        context_citations = _build_citations(context_docs)
        final_citations = list(gateway_citations)
        existing_ids = {item.get("source_id") for item in gateway_citations if item.get("source_id")}
        for citation in context_citations:
            if citation.get("source_id") not in existing_ids:
                final_citations.append(citation)
        final_citations = _filter_citations_by_response(
            final_citations, assistant_text, context_docs
        )

        return {
            "conversation_id": conversation_id,
            "message": {"role": "assistant", "content": assistant_text},
            "citations": final_citations,
            "capabilities": capabilities,
            "used_context": _build_used_context(context_docs),
        }

    def _stream_response(self):
        data = json_body(self.request) or {}
        if not isinstance(data, dict):
            raise BadRequest("JSON object expected")

        resolved_context = _resolve_context_from_payload(data)
        capabilities = _capabilities_for(resolved_context)
        payload, context_docs, last_query, messages = self._prepare_gateway_payload(data)
        last_query = last_query or ""

        response = self.request.response
        response.setHeader("Content-Type", "text/event-stream")
        response.setHeader("Cache-Control", "no-cache")
        response.setHeader("X-Accel-Buffering", "no")

        if not payload:
            missing_message = _missing_page_content_message()
            yield _sse_event("token", {"delta": missing_message})
            yield _sse_event(
                "done",
                {
                    "queue": [],
                    "message": {
                        "role": "assistant",
                        "content": missing_message,
                    },
                    "citations": [],
                    "capabilities": capabilities,
                    "used_context": _build_used_context(context_docs),
                },
            )
            return

        if _detect_site_title_intent(last_query) or _detect_page_title_intent(last_query):
            page_doc = context_docs.get("page_doc") or {}
            if _detect_site_title_intent(last_query):
                portal = api.portal.get()
                content = f"The site title is: {getattr(portal, 'Title', lambda: 'this site')()}"
                citations: List[Dict[str, Any]] = []
                for doc in context_docs.get("site_docs") or []:
                    if doc.get("type") == "site":
                        citations.append(
                            {
                                "source_id": doc.get("id"),
                                "label": doc.get("title"),
                                "url": doc.get("url"),
                                "snippet": _format_citation_snippet(doc),
                            }
                        )
                        break
            else:
                page_title = page_doc.get("title") or "this page"
                content = f"The page title is: {page_title}"
                citations = []
                if page_doc:
                    citations.append(
                        {
                            "source_id": page_doc.get("id"),
                            "label": page_title,
                            "url": page_doc.get("url"),
                            "snippet": _format_citation_snippet(page_doc),
                        }
                    )

            yield _sse_event("token", {"delta": content})
            yield _sse_event(
                "done",
                {
                    "message": {"role": "assistant", "content": content},
                    "citations": citations,
                    "capabilities": capabilities,
                    "used_context": _build_used_context(context_docs),
                },
            )
            return

        logger.info(
            "[KYRA AI CONTEXT] stream mode=%s resolved=%s text_len=%s related=%s",
            context_docs.get("mode"),
            context_docs.get("resolved"),
            context_docs.get("page_text_length"),
            len(context_docs.get("related_docs") or []),
        )

        return self._stream_events(
            payload,
            data.get("conversation_id"),
            context_docs,
            capabilities,
            last_query,
            messages,
            data,
        )

    def _stream_events(
        self,
        payload: Dict[str, Any],
        fallback_conversation_id: Optional[str],
        context_docs: Dict[str, Any],
        capabilities: Dict[str, Any],
        last_query: str,
        messages: List[Dict[str, Any]],
        original_data: Dict[str, Any],
    ) -> Iterable[str]:
        response, error = self.kyra.chat.stream(payload)
        if response is not None:
            yield from self._relay_gateway_stream(
                response,
                fallback_conversation_id,
                _build_citations(context_docs),
                _build_used_context(context_docs),
                capabilities,
                context_docs,
                last_query,
            )
            return

        prompt_response = _apply_prompt_fallback(self.kyra, messages, original_data)
        if isinstance(prompt_response, dict) and not prompt_response.get("error"):
            yield from self._simulate_stream(
                prompt_response,
                fallback_conversation_id,
                _build_citations(context_docs),
                _build_used_context(context_docs),
                capabilities,
                context_docs=context_docs,
            )
            return

        if error and _is_not_found_error(str(error)):
            fallback_response = _local_fallback_response(
                context_docs, capabilities, last_query
            )
            helper_text = fallback_response["message"]["content"]
            yield _sse_event("token", {"delta": helper_text})
            yield _sse_event(
                "done",
                {
                    "conversation_id": fallback_conversation_id,
                    "message": fallback_response["message"],
                    "citations": fallback_response["citations"],
                    "capabilities": capabilities,
                    "used_context": fallback_response.get("used_context"),
                },
            )
            return

        yield _sse_event("error", {"message": error or "Stream request failed"})
        yield _sse_event(
            "done",
            {
                "capabilities": capabilities,
                "used_context": _build_used_context(context_docs),
            },
        )

    def _relay_gateway_stream(
        self,
        response,
        fallback_conversation_id: Optional[str],
        context_citations: List[Dict[str, Any]],
        used_context: List[Dict[str, Any]],
        capabilities: Dict[str, Any],
        context_docs: Dict[str, Any],
        last_query: str,
    ) -> Iterable[str]:
        content_parts: List[str] = []
        citations: List[Dict[str, Any]] = []
        conversation_id = fallback_conversation_id
        current_event = ""

        for raw_line in response.iter_lines(decode_unicode=True):
            if not raw_line:
                current_event = ""
                continue

            if raw_line.startswith("event:"):
                current_event = raw_line.replace("event:", "").strip()
                continue

            if not raw_line.startswith("data:"):
                continue

            data_text = raw_line.replace("data:", "").strip()
            event_type, payload = _parse_gateway_stream_payload(
                current_event, data_text
            )

            if event_type == "error":
                message = payload.get("message") if isinstance(payload, dict) else payload
                yield _sse_event("error", {"message": message})
                yield _sse_event(
                    "done",
                    {
                        "capabilities": capabilities,
                        "used_context": used_context,
                    },
                )
                return

            if event_type == "citations":
                citations = (
                                payload.get("citations") if isinstance(payload, dict) else payload
                            ) or []
                yield _sse_event("citations", {"citations": citations})
                continue

            if event_type == "done":
                if isinstance(payload, dict):
                    conversation_id = _extract_conversation_id(
                        payload, conversation_id
                    )
                    payload_message = payload.get("message") or {}
                    if isinstance(payload_message, dict):
                        content = payload_message.get("content")
                        if isinstance(content, str) and content:
                            content_parts = [content]
                    payload_citations = payload.get("citations")
                    if isinstance(payload_citations, list):
                        citations = payload_citations

                assembled = "".join(content_parts)
                mode = context_docs.get("mode") or "page"
                selection_text = context_docs.get("selection_text") or ""
                needs_grounding = _needs_grounded_response(last_query, mode, context_docs)
                if _detect_smalltalk_intent(last_query):
                    needs_grounding = False
                if selection_text:
                    needs_grounding = False
                if _detect_summary_intent(last_query) and assembled and not _is_unusable_gateway_answer(assembled):
                    needs_grounding = False
                if _is_unusable_gateway_answer(assembled) or (
                    needs_grounding and not _is_grounded_answer(assembled, context_docs)
                ):
                    if needs_grounding and mode in ("search", "related"):
                        fallback = _local_fallback_response(
                            context_docs, capabilities, last_query
                        )
                    elif needs_grounding and _detect_summary_intent(last_query):
                        fallback = _local_fallback_response(
                            context_docs, capabilities, last_query
                        )
                    elif needs_grounding:
                        fallback = _site_only_response(
                            context_docs, capabilities, last_query
                        )
                    else:
                        fallback = _local_fallback_response(
                            context_docs, capabilities, last_query
                        )
                    yield _sse_event("token", {"delta": fallback["message"]["content"]})
                    yield _sse_event(
                        "done",
                        {
                            "conversation_id": conversation_id,
                            "message": fallback["message"],
                            "citations": fallback["citations"],
                            "capabilities": capabilities,
                            "used_context": fallback.get("used_context"),
                        },
                    )
                    return

                final_citations = _filter_citations_by_response(
                    citations or context_citations, assembled, context_docs
                )
                yield _sse_event(
                    "done",
                    {
                        "conversation_id": conversation_id,
                        "message": {
                            "role": "assistant",
                            "content": assembled,
                        },
                        "citations": final_citations,
                        "capabilities": capabilities,
                        "used_context": used_context,
                    },
                )
                return

            if event_type == "token":
                if isinstance(payload, dict):
                    delta = (
                        payload.get("delta")
                        or payload.get("token")
                        or payload.get("content")
                        or payload.get("text")
                        or ""
                    )
                else:
                    delta = payload

                if delta:
                    content_parts.append(delta)
                    yield _sse_event("token", {"delta": delta})

        assembled_fallback = "".join(content_parts)
        final_citations_fallback = _filter_citations_by_response(
            citations or context_citations, assembled_fallback, context_docs
        )
        yield _sse_event(
            "done",
            {
                "conversation_id": conversation_id,
                "message": {
                    "role": "assistant",
                    "content": assembled_fallback,
                },
                "citations": final_citations_fallback,
                "capabilities": capabilities,
                "used_context": used_context,
            },
        )

    def _simulate_stream(
        self,
        gateway_data: Any,
        fallback_conversation_id: Optional[str],
        context_citations: List[Dict[str, Any]],
        used_context: List[Dict[str, Any]],
        capabilities: Dict[str, Any],
        context_docs: Optional[Dict[str, Any]] = None,
    ) -> Iterable[str]:
        assistant_text = _extract_assistant_text(gateway_data)
        citations = _extract_citations(gateway_data)
        conversation_id = _extract_conversation_id(
            gateway_data, fallback_conversation_id
        )

        for chunk in _chunk_text(assistant_text, 32):
            yield _sse_event("token", {"delta": chunk})

        if citations:
            yield _sse_event("citations", {"citations": citations})

        final_citations = citations or context_citations
        if context_docs:
            final_citations = _filter_citations_by_response(
                final_citations, assistant_text, context_docs
            )
        yield _sse_event(
            "done",
            {
                "conversation_id": conversation_id,
                "message": {"role": "assistant", "content": assistant_text},
                "citations": final_citations,
                "capabilities": capabilities,
                "used_context": used_context,
            },
        )
