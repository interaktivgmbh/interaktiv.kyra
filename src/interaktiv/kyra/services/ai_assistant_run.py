from plone.restapi.services import Service
from plone.restapi.deserializer import json_body
from plone import api
from zExceptions import BadRequest

import json
import requests

PREFERRED_TEXT_KEYS = (
    "response",
    "result",
    "text",
    "output",
    "completion",
    "content",
    "message",
)

IGNORED_META_KEYS = {
    "id",
    "name",
    "prompt",
    "promptId",
    "promptName",
    "domainId",
    "modelId",
    "modelProvider",
    "createdAt",
    "updatedAt",
    "description",
    "metadata",
    "query",
    "contextUsed",
    "tokenUsage",
    "executionTimeMs",
    "model",
}

ANNOTATION_KEY = "kyra.prompts"


def _get_annotations():
    portal = api.portal.get()
    ann = portal.__annotations__
    if ANNOTATION_KEY not in ann:
        ann[ANNOTATION_KEY] = []
    return ann


def _get_stored_prompt(local_id):
    """Prompt aus den portal_annotations holen (inkl. gateway_id, falls vorhanden)."""
    if not local_id:
        return None
    ann = _get_annotations()
    prompts = ann.get(ANNOTATION_KEY, [])
    for p in prompts:
        if p.get("id") == local_id:
            return p
    return None


def _store_gateway_id(local_id, gateway_id):
    """gateway_id im gespeicherten Prompt hinterlegen, damit wir sie beim nächsten
    Aufruf nicht neu erzeugen müssen.
    """
    if not local_id or not gateway_id:
        return
    ann = _get_annotations()
    prompts = ann.get(ANNOTATION_KEY, [])
    changed = False
    for p in prompts:
        if p.get("id") == local_id:
            if p.get("gateway_id") != gateway_id:
                p["gateway_id"] = gateway_id
                changed = True
            break
    if changed:
        ann[ANNOTATION_KEY] = prompts


def _extract_text_from_data(data):
    """Versucht, einen sinnvollen Text aus einer beliebigen Gateway-Response zu ziehen,
    ohne Meta-Felder wie id/name/prompt zu verwenden.
    """

    if data is None:
        return ""

    if isinstance(data, dict):
        for key in PREFERRED_TEXT_KEYS:
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value

    def _search(obj):
        if isinstance(obj, str) and obj.strip():
            return obj

        if isinstance(obj, dict):
            for key in PREFERRED_TEXT_KEYS:
                if key in obj:
                    found = _search(obj[key])
                    if found:
                        return found

            for key, value in obj.items():
                if key in IGNORED_META_KEYS:
                    continue
                found = _search(value)
                if found:
                    return found

        if isinstance(obj, list):
            for item in obj:
                found = _search(item)
                if found:
                    return found

        return ""

    return _search(data) or ""


class AIAssistantRun(Service):
    """POST /++api++/@ai-assistant-run

    Erwartet JSON:
    {
      "prompt": { ... kompletter Prompt ... },
      "selection": "aktuell markierter Text im Editor"
    }

    Antwort:
    {
      "result": "<Text von der KI oder leerer String>",
      "actionType": "replace" | "append",
      "raw": { ... vollständige Gateway-Response ... }
    }
    """

    def reply(self):
        from .ai_assistant_settings import _get_registry, _serialize

        data = json_body(self.request) or {}

        prompt = data.get("prompt") or {}
        selection = data.get("selection", "") or ""

        if not isinstance(prompt, dict) or not prompt.get("id"):
            raise BadRequest("Missing 'prompt' object with at least an 'id' field")

        local_prompt_id = prompt.get("id")

        stored = _get_stored_prompt(local_prompt_id)
        if stored:
            merged = stored.copy()
            merged.update(prompt)
            prompt = merged

        registry = _get_registry()
        settings = _serialize(registry)

        gateway_url_base = (settings.get("gateway_url") or "").rstrip("/")
        realm_url = (settings.get("keycloak_realms_url") or "").rstrip("/")
        client_id = settings.get("keycloak_client_id") or ""
        client_secret = settings.get("keycloak_client_secret") or ""
        domain_id = settings.get("domain_id") or "plone"

        if not gateway_url_base:
            raise BadRequest("AI Gateway URL is not configured.")
        if not (realm_url and client_id and client_secret):
            raise BadRequest(
                "Keycloak Realms URL, client_id or client_secret not configured"
            )

        token_url = f"{realm_url}/protocol/openid-connect/token"
        token_res = requests.post(
            token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )
        if not token_res.ok:
            raise BadRequest(
                f"Keycloak token request failed: {token_res.status_code} {token_res.text}"
            )

        token_data = token_res.json()
        access_token = token_data.get("access_token")
        if not access_token:
            raise BadRequest("Keycloak response has no access_token")

        name = prompt.get("name") or ""
        prompt_text = prompt.get("text") or ""
        action_type = prompt.get("actionType") or "replace"

        remote_id = (
            prompt.get("gateway_id")
            or prompt.get("gatewayId")
            or local_prompt_id
        )

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "x-domain-id": domain_id,
        }

        payload = {
            "query": selection or "",
            "input": selection or "",
        }

        apply_url = f"{gateway_url_base}/{remote_id}/apply"

        gateway_res = requests.post(apply_url, json=payload, headers=headers)

        if gateway_res.status_code == 404:
            try:
                error_json = gateway_res.json()
            except Exception:
                error_json = {}
            if (
                isinstance(error_json, dict)
                and error_json.get("error") == "Prompt not found"
            ):
                create_payload = {
                    "name": name or f"Plone prompt {local_prompt_id}",
                    "prompt": prompt_text,
                }
                create_res = requests.post(
                    gateway_url_base,
                    json=create_payload,
                    headers=headers,
                )
                if create_res.ok:
                    try:
                        created = create_res.json()
                    except ValueError:
                        created = {}
                    new_id = created.get("id")
                    if new_id:
                        _store_gateway_id(local_prompt_id, new_id)
                        remote_id = new_id
                        apply_url = f"{gateway_url_base}/{remote_id}/apply"
                        gateway_res = requests.post(
                            apply_url, json=payload, headers=headers
                        )

        if not gateway_res.ok:
            raise BadRequest(
                f"AI Gateway request failed: {gateway_res.status_code} {gateway_res.text}"
            )

        try:
            gw_data = gateway_res.json() if gateway_res.content else {}
        except ValueError:
            gw_data = {"raw_text": gateway_res.text}

        try:
            print(
                "[KYRA AI] Gateway response:",
                json.dumps(gw_data, indent=2)[:4000],
            )
        except Exception:
            print("[KYRA AI] Gateway response (non-JSON)", gw_data)

        result_text = _extract_text_from_data(gw_data)

        if not isinstance(result_text, str):
            result_text = str(result_text)

        if not result_text.strip():
            print(
                "[KYRA AI] Hinweis: In der Gateway-Response wurde kein Ergebnistext "
                "gefunden (keine Keys wie 'result', 'text', 'output' etc.)."
            )

        action_type = gw_data.get("actionType") or action_type

        return {
            "result": result_text,
            "actionType": action_type,
            "raw": gw_data,
        }
