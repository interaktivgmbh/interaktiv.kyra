import json
from typing import Dict, Any, Self
from urllib.parse import parse_qs

from ZPublisher.HTTPRequest import HTTPRequest
from interaktiv.kyra.services.base import ServiceBase
from plone.dexterity.content import DexterityContent
from zope.interface import implementer
from zope.publisher.interfaces import IPublishTraverse


class PromptsGet(ServiceBase):

    page: int
    size: int

    def __init__(self, context, request):
        super().__init__(context, request)
        self.query = parse_qs(self.request.get('QUERY_STRING'))
        self.page = self.query.get('page', 1)
        self.size = self.query.get('size', 100)

    # noinspection PyMethodMayBeStatic
    def reply(self) -> Dict[str, Any]:
        response = self.kyra.prompts.list(self.page, self.size)

        return response


@implementer(IPublishTraverse)
class PromptsPost(ServiceBase):

    def __init__(self, context: DexterityContent, request: HTTPRequest) -> None:
        super().__init__(context, request)
        self.params = []

    # noinspection PyPep8Naming, PyUnusedLocal
    def publishTraverse(self, request: HTTPRequest, name: str) -> Self:
        self.params.append(name)
        return self

    def reply(self) -> Dict[str, Any]:
        prompt_id = self.params[0] if self.params else None

        if not prompt_id:
            return {
                'error': 'Missing prompt_id',
                'status': 'error'
            }

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

        response = self.kyra.prompts.apply(prompt_id, payload)
        return response


class CustomPromptPost(ServiceBase):

    def __init__(self, context: DexterityContent, request: HTTPRequest) -> None:
        super().__init__(context, request)
        self.params = []

    def reply(self) -> Dict[str, Any]:
        body = json.loads(self.request.get('BODY', '{}'))

        prompt = body.get('prompt', '')
        text = body.get('text', '')

        # Mock response for testing
        mock_response = f"[MOCK] AI processed your request.\n\nPrompt: {prompt}\n\nOriginal text: {text}\n\nThis is a simulated response. The actual implementation will call the prompt-tool API."

        return {
            'response': mock_response,
        }
