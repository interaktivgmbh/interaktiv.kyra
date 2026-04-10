import json
from typing import Any, Dict, Iterable, List, Optional, Tuple

from interaktiv.kyra import logger
from interaktiv.kyra.services.base import ServiceBase
from interaktiv.kyra.services.ai_capabilities import _capabilities_for
from interaktiv.kyra.services.ai_context import build_context_documents
from interaktiv.kyra.services.ai_chat_upload import _get_uploads_store
from interaktiv.kyra.services.ai_chat_context import (
    _apply_prompt_fallback,
    _build_citations,
    _build_system_message,
    _build_used_context,
    _filter_citations_by_response,
    _format_citation_snippet,
    _format_context_doc_message,
    _format_upload_snippet,
    _missing_page_content_message,
    _resolve_context_from_payload,
)
from interaktiv.kyra.services.ai_chat_intent import (
    _detect_content_intent,
    _detect_offsite_intent,
    _detect_page_title_intent,
    _detect_site_title_intent,
    _detect_smalltalk_intent,
    _detect_summary_intent,
    _detect_upload_intent,
    _is_grounded_answer,
    _is_not_found_error,
    _is_unusable_gateway_answer,
    _needs_grounded_response,
)
from interaktiv.kyra.services.ai_chat_fallback import (
    _answer_from_page_text,
    _answer_from_quotes,
    _local_fallback_response,
    _site_only_response,
)
from plone import api
from plone.restapi.deserializer import json_body
from zExceptions import BadRequest
from zope.interface import implementer
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


def _sse_event(event: str, payload: Any) -> str:
    if isinstance(payload, str):
        data = payload
    else:
        data = json.dumps(payload, default=str)
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
            lines = ["Uploaded files:", ""]
            for doc in upload_docs[:3]:
                snippet = _format_upload_snippet(doc)
                title = doc.get("title") or "Attachment"
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

        if _detect_offsite_intent(last_query):
            return _site_only_response(context_docs, capabilities, last_query)

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
