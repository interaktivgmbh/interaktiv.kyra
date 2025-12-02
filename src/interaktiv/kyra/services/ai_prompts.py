import json
import uuid
import time
from plone import api
from plone.restapi.services import Service
from plone.restapi.deserializer import json_body
from zope.interface import implementer
from zope.publisher.interfaces import IPublishTraverse
from zExceptions import BadRequest

ANNOTATION_KEY = "kyra.prompts"


def _get_annotations():
    """Liefert oder erzeugt die portal_annotations für kyra.prompts"""
    portal = api.portal.get()
    annotations = portal.__annotations__
    if ANNOTATION_KEY not in annotations:
        annotations[ANNOTATION_KEY] = []
    return annotations


def _save_prompts(data):
    annotations = _get_annotations()
    annotations[ANNOTATION_KEY] = data
    return data


def _get_prompts():
    annotations = _get_annotations()
    return annotations.get(ANNOTATION_KEY, [])


def _serialize_prompt(prompt):
    """Sorgt für einheitliche Ausgabe"""
    return {
        "id": prompt.get("id"),
        "name": prompt.get("name"),
        "description": prompt.get("description", ""),
        "text": prompt.get("text", ""),
        "categories": prompt.get("categories", []),
        "actionType": prompt.get("actionType", "replace"),
        "files": prompt.get("files", []),
        "created": prompt.get("created"),
        "updated": prompt.get("updated"),
    }


class AIPromptsGet(Service):
    """GET /++api++/@ai-prompts"""

    def reply(self):
        prompts = _get_prompts()
        return [_serialize_prompt(p) for p in prompts]


class AIPromptsPost(Service):
    """POST /++api++/@ai-prompts"""

    def reply(self):
        data = json_body(self.request)
        if not isinstance(data, dict):
            raise BadRequest("JSON object expected")

        name = data.get("name")
        if not name:
            raise BadRequest("Missing required field 'name'")

        new_prompt = {
            "id": str(uuid.uuid4()),
            "name": name,
            "description": data.get("description", ""),
            "text": data.get("text", ""),
            "categories": data.get("categories", []),
            "actionType": data.get("actionType", "replace"),
            "files": [],
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

        prompts = _get_prompts()
        prompts.append(new_prompt)
        _save_prompts(prompts)
        return _serialize_prompt(new_prompt)


@implementer(IPublishTraverse)
class AIPromptsPatch(Service):
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

        data = json_body(self.request)
        if not isinstance(data, dict):
            raise BadRequest("JSON object expected")

        prompts = _get_prompts()
        updated = None
        for prompt in prompts:
            if prompt.get("id") == self.prompt_id:
                prompt.update({
                    "name": data.get("name", prompt.get("name")),
                    "description": data.get("description", prompt.get("description")),
                    "text": data.get("text", prompt.get("text")),
                    "categories": data.get("categories", prompt.get("categories")),
                    "actionType": data.get("actionType", prompt.get("actionType")),
                    "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
                })
                updated = prompt
                break

        if not updated:
            raise BadRequest(f"Prompt {self.prompt_id} not found")

        _save_prompts(prompts)
        return _serialize_prompt(updated)


@implementer(IPublishTraverse)
class AIPromptsDelete(Service):
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

        prompts = _get_prompts()
        new_prompts = [p for p in prompts if p.get("id") != self.prompt_id]
        if len(new_prompts) == len(prompts):
            raise BadRequest(f"Prompt {self.prompt_id} not found")

        _save_prompts(new_prompts)
        return {"status": "deleted", "id": self.prompt_id}
