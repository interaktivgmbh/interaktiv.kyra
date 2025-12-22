import json

from interaktiv.kyra.api import KyraAPI
from plone.restapi.deserializer import json_body
from plone.restapi.services import Service
from zExceptions import BadRequest
from plone.protect.interfaces import IDisableCSRFProtection
from zope.interface import alsoProvides

def _extract_text_from_data(data):
    """Extract the gateway's main response text. Our gateway returns 'response'."""

    if isinstance(data, dict):
        value = data.get("response")
        if isinstance(value, str) and value.strip():
            return value

    if isinstance(data, str):
        return data

    return ""


def _build_prompt_payload(prompt):
    metadata = prompt.get("metadata") or {}
    categories = metadata.get("categories") or prompt.get("categories") or []
    action_type = metadata.get("action") or prompt.get("actionType") or "replace"

    payload = {
        "name": prompt.get("name") or "",
        "prompt": prompt.get("text") or prompt.get("prompt") or "",
        "categories": categories,
        "actionType": action_type,
    }
    if prompt.get("description") is not None:
        payload["description"] = prompt.get("description") or ""

    if categories:
        payload["metadata"] = {"categories": categories}
    payload.setdefault("metadata", {})["action"] = action_type

    return payload


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
        data = json_body(self.request) or {}
        alsoProvides(self.request, IDisableCSRFProtection)

        prompt = data.get("prompt") or {}
        selection = data.get("selection", "") or ""
        language = data.get("language") or ""

        if not isinstance(prompt, dict) or not prompt.get("id"):
            raise BadRequest("Missing 'prompt' object with at least an 'id' field")

        local_prompt_id = prompt.get("id")
        action_type = prompt.get("actionType") or "replace"

        remote_id = (
            prompt.get("gateway_id")
            or prompt.get("gatewayId")
            or local_prompt_id
        )

        apply_payload = {
            "query": selection or "",
            "input": selection or "",
        }
        if language:
            apply_payload["language"] = language

        kyra = KyraAPI()
        gw_data = kyra.prompts.apply(remote_id, apply_payload)

        temp_prompt_id = None
        if isinstance(gw_data, dict) and gw_data.get("error"):
            # Create a temporary prompt in the gateway, apply, then delete to avoid polluting prompt manager.
            prompt_text = prompt.get("text") or prompt.get("prompt") or ""
            if prompt_text:
                created = kyra.prompts.create(_build_prompt_payload(prompt))
                if isinstance(created, dict) and created.get("error"):
                    raise BadRequest(created.get("error"))
                temp_prompt_id = created.get("id") or created.get("_id")
                if not temp_prompt_id:
                    raise BadRequest("AI Gateway did not return a prompt id")
                gw_data = kyra.prompts.apply(temp_prompt_id, apply_payload)
                try:
                    kyra.prompts.delete(temp_prompt_id)
                except Exception:
                    pass
            if isinstance(gw_data, dict) and gw_data.get("error"):
                raise BadRequest(gw_data.get("error"))

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
            if isinstance(gw_data, dict):
                fallback_err = gw_data.get("error") or gw_data.get("message") or ""
                if fallback_err:
                    result_text = f"AI unavailable: {fallback_err}"
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
