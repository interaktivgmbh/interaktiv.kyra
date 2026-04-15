from typing import Any, Dict

from interaktiv.kyra import logger
from interaktiv.kyra.services.base import ServiceBase
from plone.restapi.deserializer import json_body
from zExceptions import BadRequest


def _extract_text_from_data(data: Any) -> str:
    """Extract the gateway's main response text."""
    if isinstance(data, dict):
        # Check message.content pattern first
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


def _build_prompt_payload(prompt: Dict[str, Any]) -> Dict[str, Any]:
    """Build a gateway-compatible prompt payload for temp prompt creation."""
    categories = prompt.get("categories") or []
    action_type = prompt.get("actionType") or "replace"

    payload = {
        "name": prompt.get("name") or "Temp prompt",
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


class AIAssistantRunService(ServiceBase):
    """POST /@ai-assistant-run

    Runs a prompt against selected text from the Slate editor
    via the Kyra gateway and returns the result.

    Request body:
        {
            "prompt": {
                "id": "...",
                "name": "...",
                "text": "Instruction text...",
                "actionType": "replace",
                "categories": [...]
            },
            "selection": "The selected text in the editor...",
            "language": "de"
        }

    Response:
        {
            "result": "Processed text...",
            "actionType": "replace"
        }
    """

    def reply(self) -> Dict[str, Any]:
        data = json_body(self.request) or {}
        if not isinstance(data, dict):
            raise BadRequest("JSON object expected")

        prompt_data = data.get("prompt") or {}
        if not isinstance(prompt_data, dict):
            raise BadRequest("Missing 'prompt' object")

        prompt_text = (
            prompt_data.get("text") or prompt_data.get("prompt") or ""
        )
        if not isinstance(prompt_text, str) or not prompt_text.strip():
            raise BadRequest("Missing 'prompt.text'")

        selection = data.get("selection") or ""
        language = data.get("language") or "en"
        action_type = prompt_data.get("actionType") or "replace"
        prompt_name = prompt_data.get("name") or "Custom instruction"
        local_prompt_id = prompt_data.get("id") or ""

        logger.info(
            "[KYRA AI ASSISTANT RUN] prompt=%s selection_len=%s lang=%s",
            prompt_name,
            len(selection),
            language,
        )

        apply_payload = {
            "query": selection or "",
            "input": selection or "",
        }
        if language:
            apply_payload["language"] = language

        remote_id = (
            prompt_data.get("gateway_id")
            or prompt_data.get("gatewayId")
            or local_prompt_id
        )

        gw_data = self.kyra.prompts.apply(remote_id, apply_payload)

        temp_prompt_id = None
        if isinstance(gw_data, dict) and gw_data.get("error"):
            if prompt_text:
                created = self.kyra.prompts.create(
                    _build_prompt_payload(prompt_data)
                )
                if isinstance(created, dict) and created.get("error"):
                    raise BadRequest(
                        f"AI gateway error: {created.get('error')}"
                    )
                temp_prompt_id = created.get("id") or created.get("_id")
                if not temp_prompt_id:
                    raise BadRequest(
                        "AI gateway did not return a prompt id"
                    )
                gw_data = self.kyra.prompts.apply(
                    temp_prompt_id, apply_payload
                )
                try:
                    self.kyra.prompts.delete(temp_prompt_id)
                except Exception:
                    pass

            if isinstance(gw_data, dict) and gw_data.get("error"):
                raise BadRequest(
                    f"AI gateway error: {gw_data.get('error')}"
                )

        result_text = _extract_text_from_data(gw_data)
        if not isinstance(result_text, str):
            result_text = str(result_text)

        if not result_text.strip():
            logger.warning(
                "[KYRA AI ASSISTANT RUN] empty result for prompt=%s",
                prompt_name,
            )

        return {
            "result": result_text,
            "actionType": action_type,
        }
