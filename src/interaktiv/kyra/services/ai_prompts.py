from typing import Any, Dict, List

from interaktiv.kyra.api import KyraAPI
from interaktiv.kyra.services.base import ServiceBase
from plone.restapi.deserializer import json_body
from zope.interface import implementer
from zope.publisher.interfaces import IPublishTraverse
from zExceptions import BadRequest


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return list(value)


def _serialize_prompt(prompt: Dict[str, Any]) -> Dict[str, Any]:
    metadata = prompt.get("metadata") or {}
    categories = _as_list(
        metadata.get("categories") or prompt.get("categories") or []
    )
    action_type = metadata.get("action") or prompt.get("actionType") or "replace"

    return {
        "id": prompt.get("id") or prompt.get("_id"),
        "name": prompt.get("name") or "",
        "description": prompt.get("description", "") or "",
        "text": prompt.get("prompt") or prompt.get("text") or "",
        "categories": categories,
        "actionType": action_type,
        "files": prompt.get("files", []),
        "created": (
            prompt.get("created")
            or prompt.get("createdAt")
            or prompt.get("created_at")
        ),
        "updated": (
            prompt.get("updated")
            or prompt.get("updatedAt")
            or prompt.get("updated_at")
        ),
    }


def _build_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise BadRequest("JSON object expected")

    name = data.get("name")
    if not name:
        raise BadRequest("Missing required field 'name'")

    action_type = data.get("actionType") or data.get("action") or "replace"
    categories = _as_list(data.get("categories"))

    payload: Dict[str, Any] = {
        "name": name,
        "prompt": data.get("text") or data.get("prompt") or "",
        "categories": categories,
        "actionType": action_type,
    }

    if "description" in data:
        payload["description"] = data.get("description") or ""

    metadata: Dict[str, Any] = {}
    if categories:
        metadata["categories"] = categories
    if action_type:
        metadata["action"] = action_type
    if metadata:
        payload["metadata"] = metadata

    return payload


class AIPromptsGet(ServiceBase):
    """GET /++api++/@ai-prompts"""

    def reply(self):
        response = self.kyra.prompts.list()
        if isinstance(response, dict) and response.get("error"):
            raise BadRequest(response.get("error"))

        prompts = []
        if isinstance(response, dict):
            prompts = response.get("prompts") or response.get("items") or []
        elif isinstance(response, list):
            prompts = response

        return [_serialize_prompt(p) for p in prompts]


class AIPromptsPost(ServiceBase):
    """POST /++api++/@ai-prompts"""

    def reply(self):
        data = json_body(self.request) or {}
        payload = _build_payload(data)

        created = self.kyra.prompts.create(payload)
        if isinstance(created, dict) and created.get("error"):
            raise BadRequest(created.get("error"))

        return _serialize_prompt(created)


@implementer(IPublishTraverse)
class AIPromptsPatch(ServiceBase):
    """PATCH /++api++/@ai-prompts/{id}"""

    def __init__(self, context, request):
        super().__init__(context, request)
        self.prompt_id = None

    def publishTraverse(self, request, name):
        self.prompt_id = name
        return self

    def reply(self):
        if not self.prompt_id:
            raise BadRequest("Missing prompt ID")

        data = json_body(self.request) or {}
        payload = _build_payload(data)

        updated = self.kyra.prompts.update(self.prompt_id, payload)
        if isinstance(updated, dict) and updated.get("error"):
            raise BadRequest(updated.get("error"))

        return _serialize_prompt(updated)


@implementer(IPublishTraverse)
class AIPromptsDelete(ServiceBase):
    """DELETE /++api++/@ai-prompts/{id}"""

    def __init__(self, context, request):
        super().__init__(context, request)
        self.prompt_id = None

    def publishTraverse(self, request, name):
        self.prompt_id = name
        return self

    def reply(self):
        if not self.prompt_id:
            raise BadRequest("Missing prompt ID")

        deleted = self.kyra.prompts.delete(self.prompt_id)
        if isinstance(deleted, dict) and deleted.get("error"):
            raise BadRequest(deleted.get("error"))

        return {"status": "deleted", "id": self.prompt_id}
