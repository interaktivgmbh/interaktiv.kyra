import json
from typing import Dict, Any, Self
from urllib.parse import parse_qs

from ZPublisher.HTTPRequest import HTTPRequest
from interaktiv.kyra.services.base import ServiceBase
from plone.dexterity.content import DexterityContent
from zope.interface import implementer
from zope.publisher.interfaces import IPublishTraverse


# 🔹 LIST + APPLY (bereits vorhanden)
class PromptsGet(ServiceBase):

    page: int
    size: int

    def __init__(self, context, request):
        super().__init__(context, request)
        self.query = parse_qs(self.request.get('QUERY_STRING'))
        self.page = self.query.get('page', 1)
        self.size = self.query.get('size', 100)

    def reply(self) -> Dict[str, Any]:
        return self.kyra.prompts.list(self.page, self.size)


@implementer(IPublishTraverse)
class PromptsPost(ServiceBase):
    """ Apply a prompt """

    def __init__(self, context: DexterityContent, request: HTTPRequest) -> None:
        super().__init__(context, request)
        self.params = []

    def publishTraverse(self, request: HTTPRequest, name: str) -> Self:
        self.params.append(name)
        return self

    def reply(self) -> Dict[str, Any]:
        prompt_id = self.params[0] if self.params else None

        if not prompt_id:
            return {'error': 'Missing prompt_id'}

        body = json.loads(self.request.get('BODY', '{}'))
        text = body.get('text')
        query = body.get('query')

        if not text or not query:
            return {'error': 'Validation Error'}

        payload = {
            'text': text,
            'query': query,
            'useContext': body.get('include_context', True)
        }

        return self.kyra.prompts.apply(prompt_id, payload)


# 🔥 NEU — Einzelnen Prompt abrufen
class PromptGet(ServiceBase):

    def __init__(self, context, request):
        super().__init__(context, request)
        self.prompt_id = request.get('prompt_id')

    def reply(self):
        if not self.prompt_id:
            return {'error': 'Missing prompt_id'}
        return self.kyra.prompts.get(self.prompt_id)


# 🔥 NEU — Prompt erstellen
class PromptCreate(ServiceBase):

    def reply(self):
        body = json.loads(self.request.get('BODY', '{}'))
        text = body.get('text')
        query = body.get('query')

        if not text or not query:
            return {'error': 'Validation Error'}

        payload = {
            'text': text,
            'query': query,
            'useContext': body.get('include_context', True)
        }

        return self.kyra.prompts.create(payload)


# 🔥 NEU — Prompt aktualisieren
@implementer(IPublishTraverse)
class PromptUpdate(ServiceBase):

    def __init__(self, context, request):
        super().__init__(context, request)
        self.params = []

    def publishTraverse(self, request, name):
        self.params.append(name)
        return self

    def reply(self):
        if not self.params:
            return {'error': 'Missing prompt_id'}

        prompt_id = self.params[0]
        body = json.loads(self.request.get('BODY', '{}'))
        return self.kyra.prompts.update(prompt_id, body)


# 🔥 NEU — Prompt löschen
@implementer(IPublishTraverse)
class PromptDelete(ServiceBase):

    def __init__(self, context, request):
        super().__init__(context, request)
        self.params = []

    def publishTraverse(self, request, name):
        self.params.append(name)
        return self

    def reply(self):
        if not self.params:
            return {'error': 'Missing prompt_id'}

        prompt_id = self.params[0]
        return self.kyra.prompts.delete(prompt_id)


# 🔥 NEU — Dateien abrufen
@implementer(IPublishTraverse)
class FilesList(ServiceBase):

    def __init__(self, context, request):
        super().__init__(context, request)
        self.params = []

    def publishTraverse(self, request, name):
        self.params.append(name)
        return self

    def reply(self):
        if not self.params:
            return {'error': 'Missing prompt_id'}

        prompt_id = self.params[0]
        return self.kyra.files.get(prompt_id)


# 🔥 NEU — Datei hochladen
@implementer(IPublishTraverse)
class FileUpload(ServiceBase):

    def __init__(self, context, request):
        super().__init__(context, request)
        self.prompt_id = None

    def publishTraverse(self, request, name):
        self.prompt_id = name
        return self

    def reply(self):
        file_obj = self.request.form.get('file')

        if not self.prompt_id:
            return {'error': 'Missing prompt_id'}

        if not file_obj:
            return {'error': 'Missing file'}

        return self.kyra.files.upload(self.prompt_id, file_obj)


# 🔥 NEU — Datei löschen
@implementer(IPublishTraverse)
class FileDelete(ServiceBase):

    def __init__(self, context, request):
        super().__init__(context, request)
        self.params = []

    def publishTraverse(self, request, name):
        self.params.append(name)
        return self

    def reply(self):
        if len(self.params) < 2:
            return {'error': 'Missing prompt_id or file_id'}

        prompt_id, file_id = self.params[0], self.params[1]
        return self.kyra.files.delete(prompt_id, file_id)
