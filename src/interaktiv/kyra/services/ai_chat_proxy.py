"""Chat proxy — forwards chat requests to the external layout-agent backend.

Same proxy as edit, but forces read-only permissions (empty list).
Injects site context into the first message for document/page awareness.
"""

from __future__ import annotations

import json
import logging

import requests
from plone.protect.interfaces import IDisableCSRFProtection
from plone.restapi.services import Service
from zope.interface import alsoProvides

from interaktiv.kyra.services.ai_edit_proxy import (
    _get_edit_backend_url,
    _get_auth_token,
    _proxy_headers,
    _inject_callbacks,
    _context_cache,
    _context_lock,
    PROXY_TIMEOUT,
)
from interaktiv.kyra.services.ai_site_context import build_site_context

logger = logging.getLogger(__name__)


class _ChatProxyBase(Service):

    def __init__(self, context, request):
        super().__init__(context, request)
        alsoProvides(self.request, IDisableCSRFProtection)

    def _forward(self, method: str, url: str, body: dict | None = None) -> dict:
        base_url = _get_edit_backend_url()
        if not base_url:
            self.request.response.setStatus(501)
            return {"error": "Edit backend not configured"}

        full_url = f"{base_url.rstrip('/')}{url}"
        token = _get_auth_token()
        headers = _proxy_headers(token)

        try:
            resp = requests.request(
                method,
                full_url,
                headers=headers,
                json=body if body is not None else None,
                timeout=PROXY_TIMEOUT,
            )
        except requests.ConnectionError:
            self.request.response.setStatus(502)
            return {"error": "Cannot connect to edit backend"}
        except requests.Timeout:
            self.request.response.setStatus(504)
            return {"error": "Edit backend timeout"}

        self.request.response.setStatus(resp.status_code)

        content_type = resp.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                return resp.json()
            except ValueError:
                pass

        if not resp.ok:
            return {"error": resp.text or f"HTTP {resp.status_code}"}

        return {"status": "ok"}


class AIChatCreateConversation(_ChatProxyBase):

    def reply(self):
        body = json.loads(self.request.get("BODY", "{}"))
        body["permissions"] = []

        _inject_callbacks(body)

        page_link = body.get("state", {}).get("link", "")
        site_context = build_site_context(page_link)

        result = self._forward("POST", "/conversations", body)

        conv_id = result.get("conversation_id")
        if conv_id and site_context:
            with _context_lock:
                _context_cache[conv_id] = site_context

        return result


class AIChatSendMessage(_ChatProxyBase):

    def reply(self):
        body = json.loads(self.request.get("BODY", "{}"))
        conversation_id = body.pop("conversation_id", None)
        if not conversation_id:
            self.request.response.setStatus(400)
            return {"error": "conversation_id is required"}

        with _context_lock:
            site_context = _context_cache.pop(conversation_id, None)

        if site_context and body.get("message"):
            body["message"] = (
                f"[Seitenkontext]\n{site_context}\n\n{body['message']}"
            )

        return self._forward(
            "POST", f"/conversations/{conversation_id}/messages", body
        )


class AIChatPollJob(_ChatProxyBase):

    def reply(self):
        job_id = self.request.get("job_id", "")
        if not job_id:
            self.request.response.setStatus(400)
            return {"error": "job_id query parameter is required"}
        return self._forward("GET", f"/jobs/{job_id}")


class AIChatCancelJob(_ChatProxyBase):

    def reply(self):
        body = json.loads(self.request.get("BODY", "{}"))
        job_id = body.get("job_id", "")
        if not job_id:
            self.request.response.setStatus(400)
            return {"error": "job_id is required"}
        return self._forward("POST", f"/jobs/{job_id}/cancel")


class AIChatGetMessages(_ChatProxyBase):

    def reply(self):
        conversation_id = self.request.get("conversation_id", "")
        if not conversation_id:
            self.request.response.setStatus(400)
            return {"error": "conversation_id is required"}
        after = self.request.get("after", "")
        url = f"/conversations/{conversation_id}/messages"
        if after:
            url += f"?after={after}"
        return self._forward("GET", url)


class AIChatGetMessage(_ChatProxyBase):

    def reply(self):
        conversation_id = self.request.get("conversation_id", "")
        message_uid = self.request.get("message_uid", "")
        if not conversation_id or not message_uid:
            self.request.response.setStatus(400)
            return {"error": "conversation_id and message_uid are required"}
        return self._forward(
            "GET", f"/conversations/{conversation_id}/messages/{message_uid}"
        )


class AIChatGetSkills(_ChatProxyBase):

    def reply(self):
        return self._forward("GET", "/skills")
