import json
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from interaktiv.kyra.services.base import ServiceBase
from interaktiv.kyra import logger
from interaktiv.kyra.services.ai_context import build_context_documents, clean_text
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
            url = url[len(portal_url) :]
        if url.startswith("/"):
            return api.content.get(path=url.lstrip("/"))

    return None


def _capabilities_for(context) -> Dict[str, Any]:
    is_anonymous = api.user.is_anonymous()
    can_edit = False
    if not is_anonymous and context is not None:
        can_edit = api.user.has_permission("Modify portal content", obj=context)

    features = ["chat"]
    if can_edit:
        features.extend(["actions_plan", "actions_apply"])

    return {
        "is_anonymous": is_anonymous,
        "can_edit": can_edit,
        "features": features,
    }


CHAT_PROMPT_CACHE_KEY = "interaktiv.kyra.ai_chat_prompt_id"


def _build_chat_prompt_payload() -> Dict[str, Any]:
    return {
        "name": "Kyra Chat",
        "prompt": (
            "You are Kyra AI, a helpful assistant for this Plone site. "
            "Answer the user's request clearly and concisely.\n\n"
            "User request:\n{{input}}"
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

    apply_payload: Dict[str, Any] = {"query": last_user, "input": last_user}
    params = data.get("params") or {}
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


MAX_DOC_MESSAGE_TEXT = 1200
CITATION_SNIPPET_LIMIT = 200


def _is_not_found_error(message: str) -> bool:
    lowered = (message or "").lower()
    return "404" in lowered or "not found" in lowered


def _build_system_message(context_docs: Dict[str, Any]) -> str:
    mode = context_docs.get("mode") or "page"
    documents = context_docs.get("documents") or []
    lines = [
        "You are Kyra AI, the helpful assistant for this Plone site.",
        f"Mode: {mode}",
        "Use ONLY the provided context documents to answer.",
        "Cite your sources (title and URL) and do not invent information.",
        "If the answer cannot be found in those documents, say you cannot find it on this website and ask what to search for next.",
        "Context documents:",
    ]
    for doc in documents[:4]:
        title = doc.get("title") or doc.get("url") or "Document"
        url = doc.get("url", "")
        snippet = (doc.get("text") or "")[:180].replace("\n", " ")
        lines.append(f"- {title} ({url}): {snippet}")
    return "\n".join(lines)


def _format_context_doc_message(doc: Dict[str, Any]) -> Dict[str, str]:
    content = f"Document: {doc.get('title')} ({doc.get('url')})\n\n{doc.get('text') or ''}"
    truncated = content[:MAX_DOC_MESSAGE_TEXT]
    return {"role": "tool", "content": truncated}


def _build_citations(context_docs: Dict[str, Any]) -> List[Dict[str, Any]]:
    mode = context_docs.get("mode") or "page"
    page_doc = context_docs.get("page_doc") or {}
    site_docs = context_docs.get("site_docs") or []
    related_docs = context_docs.get("related_docs") or []
    citation_candidates: List[Dict[str, Any]] = []
    if page_doc:
        citation_candidates.append(page_doc)
    citation_candidates.extend(site_docs)
    if mode in ("related", "search"):
        citation_candidates.extend(related_docs)

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


def _summarize_text(text: str, max_sentences: int = 3) -> str:
    cleaned = clean_text(text)
    if not cleaned:
        return ""
    separators = re.compile(r"(?<=[.!?])\s+")
    sentences = [sentence.strip() for sentence in separators.split(cleaned) if sentence.strip()]
    selected = sentences[:max_sentences] or sentences
    if not selected:
        return cleaned[:MAX_DOC_MESSAGE_TEXT]
    bullets = "\n".join(f"- {sentence}" for sentence in selected)
    return bullets


def _build_fallback_message(context_docs: Dict[str, Any], last_query: str) -> str:
    page_doc = context_docs.get("page_doc") or {}
    title = page_doc.get("title") or "This page"
    mode = context_docs.get("mode") or "page"
    query = context_docs.get("query")
    summary_text = _summarize_text(page_doc.get("text") or "")
    cleaned_query = clean_text(last_query or "")

    if not summary_text:
        return _missing_page_content_message()

    if mode == "summarize":
        return f"Summary of {title}:\n{summary_text}"
    if mode in ("related", "search"):
        label = query or title
        verb = "related content" if mode == "related" else "search results"
        return (
            f"{verb.capitalize()} for '{label}' are not reachable right now. "
            f"In the meantime, here is what I can share from {title}:\n{summary_text}"
        )
    if cleaned_query:
        return (
            f"Summary of {title} (regarding '{cleaned_query}'):\n{summary_text}\n"
            "The live AI is unavailable right now, so I’m sharing the information from this page."
        )
    return f"Summary of {title}:\n{summary_text}"


def _format_citation_snippet(doc: Dict[str, Any]) -> str:
    snippet = clean_text(doc.get("text") or "")
    if not snippet:
        snippet = clean_text(doc.get("title") or doc.get("url") or "")
    return snippet[:CITATION_SNIPPET_LIMIT].strip()


def _local_fallback_response(
    context_docs: Dict[str, Any], capabilities: Dict[str, Any], last_query: str
) -> Dict[str, Any]:
    page_doc = context_docs.get("page_doc") or {}
    citations = _build_citations(context_docs)
    summary_text = _build_fallback_message(context_docs, last_query)
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


def _sse_event(event: str, payload: Any) -> str:
    if isinstance(payload, str):
        data = payload
    else:
        data = json.dumps(payload)
    return f"event: {event}\ndata: {data}\n\n"


def _chunk_text(text: str, size: int = 32) -> Iterable[str]:
    if not text:
        return []
    return [text[i : i + size] for i in range(0, len(text), size)]


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
        context_docs = build_context_documents(data.get("context"))
        page_text = context_docs.get("page_doc", {}).get("text", "")
        if not page_text:
            return None, context_docs, "", messages

        system_message = _build_system_message(context_docs)
        doc_messages = [
            _format_context_doc_message(doc)
            for doc in (context_docs.get("documents") or [])[:3]
        ]
        gateway_messages = [{"role": "system", "content": system_message}] + doc_messages + messages
        payload, last_user = _build_gateway_payload(data, gateway_messages)
        payload["context_documents"] = context_docs.get("documents", [])
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
        logger.debug("[KYRA AI GATEWAY RESPONSE] %s", gateway_data)

        if isinstance(gateway_data, dict) and gateway_data.get("error"):
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
        if not assistant_text:
            prompt_response = _apply_prompt_fallback(self.kyra, messages_with_context, data)
            if isinstance(prompt_response, dict) and not prompt_response.get("error"):
                gateway_data = prompt_response
                assistant_text = _extract_assistant_text(gateway_data)

        if not assistant_text:
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

                yield _sse_event(
                    "done",
                    {
                        "conversation_id": conversation_id,
                        "message": {
                            "role": "assistant",
                            "content": "".join(content_parts),
                        },
                        "citations": citations or context_citations,
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

        yield _sse_event(
            "done",
            {
                "conversation_id": conversation_id,
                "message": {
                    "role": "assistant",
                    "content": "".join(content_parts),
                },
                "citations": citations or context_citations,
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

        yield _sse_event(
            "done",
            {
                "conversation_id": conversation_id,
                "message": {"role": "assistant", "content": assistant_text},
                "citations": citations or context_citations,
                "capabilities": capabilities,
                "used_context": used_context,
            },
        )
