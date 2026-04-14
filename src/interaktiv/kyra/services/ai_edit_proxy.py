"""Edit proxy — forwards requests to the external layout-agent backend.

Enriches conversation-creation requests with callback URLs (when Plone is
reachable) and injects a local site-context snapshot into the first message
so the agent always knows about the site structure and available documents.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

import requests
from plone import api
from plone.protect.interfaces import IDisableCSRFProtection
from plone.restapi.services import Service
from zope.interface import alsoProvides

from interaktiv.kyra.registry.ai_assistant import IAIAssistantSchema
from interaktiv.kyra.services.ai_site_context import build_site_context

logger = logging.getLogger(__name__)

PROXY_TIMEOUT = 60

# Per-conversation context cache (conversation_id → site context string).
# Populated at conversation creation, consumed on first message.
_context_cache: dict[str, str] = {}
_context_lock = threading.Lock()


def _get_edit_backend_url() -> str:
    return (
        api.portal.get_registry_record(
            name="edit_backend_url", interface=IAIAssistantSchema
        )
        or ""
    )


def _get_auth_token() -> str:
    try:
        from interaktiv.kyra.api.base import APIBase

        base = APIBase()
        return base.token or ""
    except Exception:
        logger.debug("Could not obtain Keycloak token for edit backend", exc_info=True)
        return ""


def _proxy_headers(token: str) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _inject_callbacks(body: dict) -> None:
    """Add callback URLs + token to a create-conversation payload.

    The layout-agent uses these to query Plone for pages, search, etc.
    If no Keycloak token is available, callbacks are skipped — the agent
    still works using the pre-loaded site context instead.
    """
    token = _get_auth_token()
    if not token:
        return

    portal = api.portal.get()
    base = portal.absolute_url()
    api_base = f"{base}/++api++"

    body["callbacks"] = {
        "get_page": f"{api_base}/@ai-callback-page",
        "get_metadata": f"{api_base}/@ai-callback-metadata",
        "list_children": f"{api_base}/@ai-callback-children",
        "search_content": f"{api_base}/@ai-callback-search",
        "get_breadcrumb": f"{api_base}/@ai-callback-breadcrumb",
        "search_documents": f"{api_base}/@ai-callback-documents-search",
        "read_document_pages": f"{api_base}/@ai-callback-documents-read",
        "view_image": f"{api_base}/@ai-callback-image",
    }
    body["callback_access_token"] = token


class _EditProxyBase(Service):

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


class AIEditCreateConversation(_EditProxyBase):

    def reply(self):
        body = json.loads(self.request.get("BODY", "{}"))

        _inject_callbacks(body)

        page_link = body.get("state", {}).get("link", "")
        site_context = build_site_context(page_link)

        result = self._forward("POST", "/conversations", body)

        conv_id = result.get("conversation_id")
        if conv_id and site_context:
            with _context_lock:
                _context_cache[conv_id] = site_context

        return result


class AIEditSendMessage(_EditProxyBase):

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


class AIEditPollJob(_EditProxyBase):

    def reply(self):
        job_id = self.request.get("job_id", "")
        if not job_id:
            self.request.response.setStatus(400)
            return {"error": "job_id query parameter is required"}
        return self._forward("GET", f"/jobs/{job_id}")


class AIEditCancelJob(_EditProxyBase):

    def reply(self):
        body = json.loads(self.request.get("BODY", "{}"))
        job_id = body.get("job_id", "")
        if not job_id:
            self.request.response.setStatus(400)
            return {"error": "job_id is required"}
        return self._forward("POST", f"/jobs/{job_id}/cancel")


class AIEditGetMessages(_EditProxyBase):

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


class AIEditGetMessage(_EditProxyBase):

    def reply(self):
        conversation_id = self.request.get("conversation_id", "")
        message_uid = self.request.get("message_uid", "")
        if not conversation_id or not message_uid:
            self.request.response.setStatus(400)
            return {"error": "conversation_id and message_uid are required"}
        return self._forward(
            "GET", f"/conversations/{conversation_id}/messages/{message_uid}"
        )


class AIEditGetSkills(_EditProxyBase):

    def reply(self):
        return self._forward("GET", "/skills")
