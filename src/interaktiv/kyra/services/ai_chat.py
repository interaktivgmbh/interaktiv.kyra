import json
from typing import Any, Dict, Iterable, List, Optional, Tuple

from interaktiv.kyra.services.base import ServiceBase
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


def _build_gateway_payload(data: Dict[str, Any], messages: List[Dict[str, Any]]) -> Dict[str, Any]:
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
    return payload


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


def _is_not_found_error(message: str) -> bool:
    lowered = (message or "").lower()
    return "404" in lowered or "not found" in lowered


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

    def reply(self):
        data = json_body(self.request) or {}
        if not isinstance(data, dict):
            raise BadRequest("JSON object expected")

        messages = _validate_messages(data)
        payload = _build_gateway_payload(data, messages)
        gateway_data = self.kyra.chat.send(payload)
        if isinstance(gateway_data, dict) and gateway_data.get("error"):
            error_message = gateway_data.get("error")
            if _is_not_found_error(str(error_message)):
                gateway_data = _apply_prompt_fallback(self.kyra, messages, data)
                if isinstance(gateway_data, dict) and gateway_data.get("error"):
                    raise BadRequest(gateway_data.get("error"))
            else:
                raise BadRequest(error_message)

        assistant_text = _extract_assistant_text(gateway_data)
        citations = _extract_citations(gateway_data)
        conversation_id = _extract_conversation_id(
            gateway_data, data.get("conversation_id")
        )

        return {
            "conversation_id": conversation_id,
            "message": {"role": "assistant", "content": assistant_text},
            "citations": citations,
            "capabilities": _capabilities_for(_resolve_context_from_payload(data)),
        }

    def _stream_response(self):
        data = json_body(self.request) or {}
        if not isinstance(data, dict):
            raise BadRequest("JSON object expected")

        messages = _validate_messages(data)
        payload = _build_gateway_payload(data, messages)

        response = self.request.response
        response.setHeader("Content-Type", "text/event-stream")
        response.setHeader("Cache-Control", "no-cache")
        response.setHeader("X-Accel-Buffering", "no")

        return self._stream_events(payload, data.get("conversation_id"))

    def _stream_events(
        self, payload: Dict[str, Any], fallback_conversation_id: Optional[str]
    ) -> Iterable[str]:
        response, error = self.kyra.chat.stream(payload)
        if response is not None:
            yield from self._relay_gateway_stream(response, fallback_conversation_id)
            return

        if error and _is_not_found_error(str(error)):
            messages = payload.get("messages") or []
            params = payload.get("params") or {}
            fallback_data = _apply_prompt_fallback(
                self.kyra, messages, {"params": params}
            )
            if isinstance(fallback_data, dict) and fallback_data.get("error"):
                yield _sse_event("error", {"message": fallback_data.get("error")})
                yield _sse_event(
                    "done",
                    {
                        "capabilities": _capabilities_for(
                            _resolve_context_from_payload(
                                {"context": payload.get("context")}
                            )
                        )
                    },
                )
                return
            yield from self._simulate_stream(fallback_data, fallback_conversation_id)
            return

        yield _sse_event("error", {"message": error or "Stream request failed"})
        yield _sse_event(
            "done",
            {
                "capabilities": _capabilities_for(
                    _resolve_context_from_payload(
                        {"context": payload.get("context")}
                    )
                )
            },
        )

    def _relay_gateway_stream(
        self, response, fallback_conversation_id: Optional[str]
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
                yield _sse_event("done", {"capabilities": _capabilities_for(None)})
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
                        "citations": citations,
                        "capabilities": _capabilities_for(None),
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
                "citations": citations,
                "capabilities": _capabilities_for(None),
            },
        )

    def _simulate_stream(
        self, gateway_data: Any, fallback_conversation_id: Optional[str]
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
                "citations": citations,
                "capabilities": _capabilities_for(None),
            },
        )
