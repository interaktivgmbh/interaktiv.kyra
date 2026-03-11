"""Proxy for the external Layout Agent API.

Forwards layout-agent requests from the frontend through Plone so the
browser never talks directly to the external service (avoids CORS issues)
and keeps credentials server-side.

Endpoints:
  POST /@ai-edit-conversations          → POST {edit_backend_url}/conversations
  POST /@ai-edit-messages               → POST {edit_backend_url}/conversations/{id}/messages
  GET  /@ai-edit-jobs                    → GET  {edit_backend_url}/jobs/{id}
  POST /@ai-edit-job-cancel             → POST {edit_backend_url}/jobs/{id}/cancel
"""

import json
import logging

import requests
from plone import api
from plone.protect.interfaces import IDisableCSRFProtection
from plone.restapi.services import Service
from zope.interface import alsoProvides

from interaktiv.kyra.registry.ai_assistant import IAIAssistantSchema

logger = logging.getLogger(__name__)

PROXY_TIMEOUT = 60


def _get_edit_backend_url() -> str:
    return (
        api.portal.get_registry_record(
            name="edit_backend_url", interface=IAIAssistantSchema
        )
        or ""
    )


def _get_auth_token() -> str:
    """Return a Bearer token for the layout agent.

    Prefers the static ``edit_backend_api_key`` registry value.  When empty,
    falls back to a Keycloak JWT obtained through the gateway credentials.
    """
    static_key = api.portal.get_registry_record(
        name="edit_backend_api_key", interface=IAIAssistantSchema
    ) or ""
    if static_key:
        return static_key

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


def _error_reply(message: str, status: int = 502):
    return {"error": message, "_status": status}


class _EditProxyBase(Service):
    """Shared base for all edit-proxy endpoints."""

    def __init__(self, context, request):
        super().__init__(context, request)
        alsoProvides(self.request, IDisableCSRFProtection)

    def _forward(self, method: str, url: str, body: dict | None = None) -> dict:
        base_url = _get_edit_backend_url()
        if not base_url:
            self.request.response.setStatus(501)
            return {"error": "Edit backend not configured"}

        full_url = f"{base_url}{url}"
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
        logger.info(
            "[ai-edit-proxy] %s %s → %s (%s bytes)",
            method, full_url, resp.status_code, len(resp.content),
        )

        content_type = resp.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                data = resp.json()
                # Log state keys for completed jobs to aid debugging
                if isinstance(data, dict) and data.get("status") == "completed":
                    state_keys = list(data.get("state", {}).keys()) if isinstance(data.get("state"), dict) else "no state"
                    logger.info("[ai-edit-proxy] Completed job state keys: %s", state_keys)
                return data
            except ValueError:
                pass

        if not resp.ok:
            return {"error": resp.text or f"HTTP {resp.status_code}"}

        return {"status": "ok"}


# ── POST /@ai-edit-conversations ──────────────────────────────────────────


class AIEditCreateConversation(_EditProxyBase):
    """POST /@ai-edit-conversations

    Body: { schema, version, state }
    Proxies to POST {edit_backend_url}/conversations
    """

    def reply(self):
        body = json.loads(self.request.get("BODY", "{}"))
        return self._forward("POST", "/conversations", body)


# ── POST /@ai-edit-messages ───────────────────────────────────────────────


class AIEditSendMessage(_EditProxyBase):
    """POST /@ai-edit-messages

    Body: { conversation_id, message, ... }
    Proxies to POST {edit_backend_url}/conversations/{id}/messages
    """

    def reply(self):
        body = json.loads(self.request.get("BODY", "{}"))
        conversation_id = body.pop("conversation_id", None)
        if not conversation_id:
            self.request.response.setStatus(400)
            return {"error": "conversation_id is required"}
        return self._forward(
            "POST", f"/conversations/{conversation_id}/messages", body
        )


# ── GET /@ai-edit-jobs ────────────────────────────────────────────────────


class AIEditPollJob(_EditProxyBase):
    """GET /@ai-edit-jobs?job_id=...

    Proxies to GET {edit_backend_url}/jobs/{id}
    """

    def reply(self):
        job_id = self.request.get("job_id", "")
        if not job_id:
            self.request.response.setStatus(400)
            return {"error": "job_id query parameter is required"}
        return self._forward("GET", f"/jobs/{job_id}")


# ── POST /@ai-edit-job-cancel ─────────────────────────────────────────────


class AIEditCancelJob(_EditProxyBase):
    """POST /@ai-edit-job-cancel

    Body: { job_id }
    Proxies to POST {edit_backend_url}/jobs/{id}/cancel
    """

    def reply(self):
        body = json.loads(self.request.get("BODY", "{}"))
        job_id = body.get("job_id", "")
        if not job_id:
            self.request.response.setStatus(400)
            return {"error": "job_id is required"}
        return self._forward("POST", f"/jobs/{job_id}/cancel")
