from typing import Any, Dict, Optional

from plone import api
from plone.protect.interfaces import IDisableCSRFProtection
from plone.restapi.deserializer import json_body
from plone.restapi.services import Service
from zExceptions import BadRequest
from zope.annotation.interfaces import IAnnotations
from zope.interface import alsoProvides

TAG_MAPPINGS_KEY = "interaktiv.kyra.tag_mappings"


def _get_tag_mappings() -> Dict[str, Dict[str, str]]:
    portal = api.portal.get()
    annotations = IAnnotations(portal)
    return annotations.get(TAG_MAPPINGS_KEY) or {}


def _set_tag_mappings(mappings: Dict[str, Dict[str, str]]) -> None:
    portal = api.portal.get()
    annotations = IAnnotations(portal)
    annotations[TAG_MAPPINGS_KEY] = mappings


class AITagMappingsService(Service):

    def __init__(self, context, request):
        super().__init__(context, request)
        alsoProvides(self.request, IDisableCSRFProtection)

    def reply(self):
        method = self.request.method.upper()
        if method == "GET":
            return self._handle_get()
        if method == "POST":
            return self._handle_post()
        if method == "DELETE":
            return self._handle_delete()
        raise BadRequest("Unsupported method")

    def _handle_get(self):
        mappings = _get_tag_mappings()
        return {"mappings": mappings}

    def _handle_post(self):
        data = json_body(self.request) or {}
        tag = data.get("tag")
        language = data.get("language")
        translated = data.get("translated")

        if not isinstance(tag, str) or not tag.strip():
            raise BadRequest("Missing 'tag'")
        if not isinstance(language, str) or not language.strip():
            raise BadRequest("Missing 'language'")
        if not isinstance(translated, str) or not translated.strip():
            raise BadRequest("Missing 'translated'")

        tag = tag.strip()
        language = language.strip().lower()
        translated = translated.strip()

        mappings = _get_tag_mappings()
        if not isinstance(mappings, dict):
            mappings = {}

        if tag not in mappings:
            mappings[tag] = {}
        mappings[tag][language] = translated
        _set_tag_mappings(mappings)

        return {"result": "ok", "mappings": mappings}

    def _handle_delete(self):
        data = json_body(self.request) or {}
        tag = data.get("tag")

        if not isinstance(tag, str) or not tag.strip():
            raise BadRequest("Missing 'tag'")

        tag = tag.strip()
        language = data.get("language")

        mappings = _get_tag_mappings()
        if not isinstance(mappings, dict):
            mappings = {}

        if tag not in mappings:
            return {"result": "ok", "mappings": mappings}

        if isinstance(language, str) and language.strip():
            mappings[tag].pop(language.strip().lower(), None)
            if not mappings[tag]:
                del mappings[tag]
        else:
            del mappings[tag]

        _set_tag_mappings(mappings)
        return {"result": "ok", "mappings": mappings}
