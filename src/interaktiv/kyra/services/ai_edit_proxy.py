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


class _EditProxyBase(Service):

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

        logger.info(
            "[ai-edit-proxy] >>> %s %s | auth=%s | body keys=%s",
            method, full_url,
            "Bearer <key>" if token else "NONE",
            list(body.keys()) if body else "no body",
        )
        if body:
            debug_body = {k: (f"<{len(json.dumps(v))} chars>" if k == "state" else v) for k, v in body.items()}
            logger.info("[ai-edit-proxy] >>> body: %s", json.dumps(debug_body, ensure_ascii=False))

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
                if isinstance(data, dict) and data.get("status") == "completed":
                    state_keys = list(data.get("state", {}).keys()) if isinstance(data.get("state"), dict) else "no state"
                    logger.info("[ai-edit-proxy] Completed job state keys: %s", state_keys)
                    if isinstance(data.get("state"), dict):
                        logger.info("[ai-edit-proxy] FULL STATE: %s", json.dumps(data["state"], ensure_ascii=False))
                return data
            except ValueError:
                pass

        if not resp.ok:
            return {"error": resp.text or f"HTTP {resp.status_code}"}

        return {"status": "ok"}


class AIEditCreateConversation(_EditProxyBase):

    def reply(self):
        body = json.loads(self.request.get("BODY", "{}"))
        return self._forward("POST", "/conversations", body)


class AIEditSendMessage(_EditProxyBase):

    def reply(self):
        body = json.loads(self.request.get("BODY", "{}"))
        conversation_id = body.pop("conversation_id", None)
        if not conversation_id:
            self.request.response.setStatus(400)
            return {"error": "conversation_id is required"}
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
