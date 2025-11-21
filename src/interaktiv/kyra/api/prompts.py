"""Client for prompt-related operations in Kyra API."""

from typing import Any, Dict

from interaktiv.kyra.api.base import APIBase
from interaktiv.kyra.api.types import PromptData, InstructionData


class Prompts(APIBase):
    """Provides methods to create, read, update, delete, and apply prompts
    for AI-assisted content generation and processing.
    """

    def list(self, page: int = 1, size: int = 100) -> Dict[str, Any]:
        """Retrieve paginated list of prompts."""
        params = {'page': page, 'size': size}
        response = self.request('GET', self.gateway_url, params=params)
        return response

    def get(self, prompt_id: str) -> Dict[str, Any]:
        """Retrieve a single prompt by ID."""
        url = f'{self.gateway_url}/{prompt_id}'
        return self.request('GET', url)

    def create(self, payload: PromptData) -> Dict[str, Any]:
        """Create a new prompt."""
        response = self.request('POST', self.gateway_url, json=payload)
        return response

    def update(self, prompt_id: str, payload: PromptData) -> Dict[str, Any]:
        """Update an existing prompt."""
        url = f'{self.gateway_url}/{prompt_id}'
        response = self.request('PATCH', url, json=payload)
        return response

    def delete(self, prompt_id: str) -> Dict[str, Any]:
        """Delete a prompt."""
        url = f'{self.gateway_url}/{prompt_id}'
        response = self.request('DELETE', url)
        return response

    def apply(self, prompt_id: str, payload: InstructionData) -> Dict[str, Any]:
        """Apply a prompt and return AI-generated result."""
        url = f'{self.gateway_url}/{prompt_id}/apply'
        response = self.request('POST', url, json=payload)
        return response
