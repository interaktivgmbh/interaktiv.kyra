from typing import Any, Dict

from interaktiv.kyra.api.base import APIBase


class Prompts(APIBase):

    def create(self, payload: dict) -> Dict[str, Any]:
        response = self.request('POST', self.gateway_url, json=payload)
        return response

    def apply(self, prompt_id: str, payload: dict) -> Dict[str, Any]:
        url = f'{self.gateway_url}/{prompt_id}/apply'
        response = self.request('POST', url, json=payload)
        return response
