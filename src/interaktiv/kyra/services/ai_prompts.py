import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from interaktiv.kyra import logger
from plone import api
from plone.protect.interfaces import IDisableCSRFProtection
from plone.restapi.deserializer import json_body
from plone.restapi.services import Service
from zExceptions import BadRequest
from zope.annotation.interfaces import IAnnotations
from zope.interface import alsoProvides

PROMPTS_STORAGE_KEY = "interaktiv.kyra.prompts"


def _get_prompts_store() -> Dict[str, Dict[str, Any]]:
    portal = api.portal.get()
    annotations = IAnnotations(portal)
    store = annotations.get(PROMPTS_STORAGE_KEY)
    if not isinstance(store, dict):
        store = {}
        annotations[PROMPTS_STORAGE_KEY] = store
    return store


def _persist_store(store: Dict[str, Dict[str, Any]]) -> None:
    portal = api.portal.get()
    annotations = IAnnotations(portal)
    annotations[PROMPTS_STORAGE_KEY] = store


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return list(value)


def list_prompts() -> List[Dict[str, Any]]:
    store = _get_prompts_store()
    prompts = list(store.values())
    prompts.sort(key=lambda p: p.get("name", "").lower())
    return prompts


def get_prompt(prompt_id: str) -> Optional[Dict[str, Any]]:
    store = _get_prompts_store()
    return store.get(prompt_id)


def create_prompt(data: Dict[str, Any]) -> Dict[str, Any]:
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise BadRequest("Missing required field 'name'")

    text = data.get("text") or data.get("prompt") or ""
    if not isinstance(text, str) or not text.strip():
        raise BadRequest("Missing required field 'text'")

    prompt_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()

    prompt = {
        "id": prompt_id,
        "name": name.strip(),
        "description": (data.get("description") or "").strip(),
        "text": text.strip(),
        "categories": _as_list(data.get("categories")),
        "actionType": data.get("actionType") or "replace",
        "created": now,
        "updated": now,
    }

    store = _get_prompts_store()
    store[prompt_id] = prompt
    _persist_store(store)

    logger.info("[KYRA PROMPTS] created prompt %s: %s", prompt_id, name)
    return prompt


def update_prompt(prompt_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    store = _get_prompts_store()
    prompt = store.get(prompt_id)
    if prompt is None:
        raise BadRequest(f"Prompt '{prompt_id}' not found")

    if "name" in data and data["name"]:
        prompt["name"] = data["name"].strip()
    if "text" in data or "prompt" in data:
        text = data.get("text") or data.get("prompt") or ""
        if text.strip():
            prompt["text"] = text.strip()
    if "description" in data:
        prompt["description"] = (data["description"] or "").strip()
    if "categories" in data:
        prompt["categories"] = _as_list(data["categories"])
    if "actionType" in data:
        prompt["actionType"] = data["actionType"] or "replace"

    prompt["updated"] = datetime.utcnow().isoformat()
    store[prompt_id] = prompt
    _persist_store(store)

    logger.info("[KYRA PROMPTS] updated prompt %s", prompt_id)
    return prompt


def delete_prompt(prompt_id: str) -> None:
    store = _get_prompts_store()
    if prompt_id not in store:
        raise BadRequest(f"Prompt '{prompt_id}' not found")
    del store[prompt_id]
    _persist_store(store)

    from interaktiv.kyra.services.prompt_files import delete_files_for_prompt
    delete_files_for_prompt(prompt_id)

    logger.info("[KYRA PROMPTS] deleted prompt %s", prompt_id)


class AIPromptsService(Service):

    def __init__(self, context, request):
        super().__init__(context, request)
        alsoProvides(self.request, IDisableCSRFProtection)

    def reply(self):
        method = self.request.method.upper()
        if method == "GET":
            return self._handle_get()
        if method == "POST":
            return self._handle_post()
        if method == "PATCH":
            return self._handle_patch()
        if method == "DELETE":
            return self._handle_delete()
        raise BadRequest("Unsupported method")

    def _handle_get(self):
        return {"prompts": list_prompts()}

    def _handle_post(self):
        data = json_body(self.request) or {}
        prompt = create_prompt(data)
        return {"result": "ok", "prompt": prompt}

    def _handle_patch(self):
        data = json_body(self.request) or {}
        prompt_id = data.get("id")
        if not prompt_id:
            raise BadRequest("Missing 'id'")
        prompt = update_prompt(prompt_id, data)
        return {"result": "ok", "prompt": prompt}

    def _handle_delete(self):
        data = json_body(self.request) or {}
        prompt_id = data.get("id")
        if not prompt_id:
            raise BadRequest("Missing 'id'")
        delete_prompt(prompt_id)
        return {"result": "ok", "id": prompt_id}
