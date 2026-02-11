from typing import Any, Dict

from interaktiv.kyra.api.base import APIBase
from interaktiv.kyra.api.types import PromptData, InstructionData


class Prompts(APIBase):

    def list(self, page=None, size=None) -> Dict[str, Any]:
        params = {}
        if page is not None:
            params['page'] = page
        if size is not None:
            params['size'] = size
        response = self.request('GET', self.gateway_url, params=params)
        return response

    def get(self, prompt_id: str) -> Dict[str, Any]:
        url = f'{self.gateway_url}/{prompt_id}'
        return self.request('GET', url)

    def create(self, payload: dict) -> Dict[str, Any]:
        response = self.request('POST', self.gateway_url, json=payload)
        return response

    def update(self, prompt_id: str, payload: dict) -> Dict[str, Any]:
        url = f'{self.gateway_url}/{prompt_id}'
        response = self.request('PATCH', url, json=payload)
        return response

    def delete(self, prompt_id: str) -> Dict[str, Any]:
        url = f'{self.gateway_url}/{prompt_id}'
        response = self.request('DELETE', url)
        return response

    def apply(self, prompt_id: str, payload: dict) -> Dict[str, Any]:
        url = f'{self.gateway_url}/{prompt_id}/apply'
        response = self.request('POST', url, json=payload)
        return response
