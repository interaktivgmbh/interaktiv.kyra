import json
import logging
from typing import Any, Dict, List, Optional

from plone import api
from plone.protect.interfaces import IDisableCSRFProtection
from plone.restapi.services import Service
from zExceptions import BadRequest, NotFound
from zope.annotation.interfaces import IAnnotations
from zope.interface import alsoProvides, implementer
from zope.publisher.interfaces import IPublishTraverse

logger = logging.getLogger(__name__)

STORAGE_KEY = "interaktiv.kyra.user_chat_data"


def _read_body(request) -> dict:
    """Read JSON body from request, trying multiple methods."""
    # Try plone.restapi's json_body first
    try:
        from plone.restapi.deserializer import json_body
        data = json_body(request)
        if data:
            return data
    except Exception:
        pass
    # Fallback: read BODY directly (works in SSR/proxy contexts)
    raw = request.get("BODY", b"")
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    if raw:
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            pass
    return {}


def _get_user_id() -> str:
    user = api.user.get_current()
    return user.getId() or ""


def _get_user_store(user_id: str) -> Dict[str, Any]:
    portal = api.portal.get()
    annotations = IAnnotations(portal)
    all_data = annotations.get(STORAGE_KEY)
    if not isinstance(all_data, dict):
        all_data = {}
        annotations[STORAGE_KEY] = all_data
    user_data = all_data.get(user_id)
    if not isinstance(user_data, dict):
        user_data = {"conversations": [], "chat_name": None}
        all_data[user_id] = user_data
    return user_data


def _persist_user_store(user_id: str, data: Dict[str, Any]) -> None:
    portal = api.portal.get()
    annotations = IAnnotations(portal)
    all_data = annotations.get(STORAGE_KEY, {})
    all_data[user_id] = data
    annotations[STORAGE_KEY] = all_data


def _sort_conversations(conversations: List[Dict]) -> List[Dict]:
    def sort_key(c):
        pinned = bool(c.get("pinned", False))
        updated = c.get("updatedAt", "")
        return (not pinned, updated)
    return sorted(conversations, key=sort_key, reverse=False)


class AIChatHistoryGet(Service):
    """GET /@ai-chat-history — load conversations and chat_name for current user."""

    def __init__(self, context, request):
        super().__init__(context, request)
        alsoProvides(self.request, IDisableCSRFProtection)

    def reply(self):
        user_id = _get_user_id()
        if not user_id:
            return {"conversations": [], "chat_name": None}
        data = _get_user_store(user_id)
        return {
            "conversations": data.get("conversations", []),
            "chat_name": data.get("chat_name"),
        }


class AIChatHistoryPatch(Service):
    """PATCH /@ai-chat-history — upsert a single conversation and/or update chat_name."""

    def __init__(self, context, request):
        super().__init__(context, request)
        alsoProvides(self.request, IDisableCSRFProtection)

    def reply(self):
        user_id = _get_user_id()
        if not user_id:
            raise BadRequest("Authentication required")

        body = _read_body(self.request)
        data = _get_user_store(user_id)

        # Upsert single conversation
        conversation = body.get("conversation")
        if isinstance(conversation, dict) and conversation.get("id"):
            conversations = data.get("conversations", [])
            conv_id = conversation["id"]
            conversations = [c for c in conversations if c.get("id") != conv_id]
            conversations.insert(0, conversation)
            data["conversations"] = conversations

        # Update chat_name
        if "chat_name" in body:
            data["chat_name"] = body["chat_name"]

        _persist_user_store(user_id, data)
        return {"status": "ok"}


class AIChatHistoryPut(Service):
    """PUT /@ai-chat-history — bulk replace all conversations and chat_name."""

    def __init__(self, context, request):
        super().__init__(context, request)
        alsoProvides(self.request, IDisableCSRFProtection)

    def reply(self):
        user_id = _get_user_id()
        if not user_id:
            raise BadRequest("Authentication required")

        body = _read_body(self.request)
        data = {
            "conversations": body.get("conversations", []),
            "chat_name": body.get("chat_name"),
        }
        _persist_user_store(user_id, data)
        return {"status": "ok"}


@implementer(IPublishTraverse)
class AIChatHistoryDelete(Service):
    """DELETE /@ai-chat-history/{conversation_id} — delete one conversation."""

    def __init__(self, context, request):
        super().__init__(context, request)
        alsoProvides(self.request, IDisableCSRFProtection)
        self._conversation_id = None

    def publishTraverse(self, request, name):
        self._conversation_id = name
        return self

    def reply(self):
        user_id = _get_user_id()
        if not user_id:
            raise BadRequest("Authentication required")

        conv_id = self._conversation_id
        if not conv_id:
            raise BadRequest("conversation_id is required in URL path")

        data = _get_user_store(user_id)
        conversations = data.get("conversations", [])
        original_len = len(conversations)
        data["conversations"] = [c for c in conversations if c.get("id") != conv_id]

        if len(data["conversations"]) == original_len:
            raise NotFound(f"Conversation {conv_id} not found")

        _persist_user_store(user_id, data)
        return {"status": "ok"}
