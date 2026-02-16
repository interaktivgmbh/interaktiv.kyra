from typing import Any, Dict, Optional, Tuple

import requests
from interaktiv.kyra.api.base import APIBase


class Chat(APIBase):

    def _chat_url(self):
        gateway_url = self.gateway_url
        if not gateway_url:
            return ''
        gateway_url = gateway_url.rstrip('/')
        if gateway_url.endswith('/prompts'):
            gateway_url = gateway_url[:len(gateway_url) - len('/prompts')]
        return gateway_url + '/chat'

    def _fallback_chat_url(self):
        if not self.gateway_url:
            return ''
        return self.gateway_url.rstrip('/')

    def _get_chat_headers(self, include_content_type=True):
        headers = {}
        domain_id = self._get_domain_id()
        if domain_id:
            headers['x-domain-id'] = domain_id
        if self.token:
            headers['Authorization'] = f'Bearer {self.token}'
        if include_content_type:
            headers['Content-Type'] = 'application/json'
        return headers

    def send(self, payload) -> Dict[str, Any]:
        url = self._chat_url()
        headers = self._get_chat_headers()
        if not headers:
            return {'error': 'No headers available'}

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=60)
            response.raise_for_status()
            return response.json()
        except requests.HTTPError as e:
            message = str(e)
            if '404' in message.lower() or 'not found' in message.lower():
                fallback = self._fallback_chat_url()
                try:
                    response = requests.post(fallback, headers=headers, json=payload, timeout=60)
                    response.raise_for_status()
                    return response.json()
                except Exception:
                    pass
            return {'error': message}
        except requests.Timeout:
            return {'error': 'Request timeout - please try again'}
        except requests.ConnectionError:
            return {'error': 'Cannot connect to API service'}
        except Exception as e:
            return {'error': f'Request failed: {e}'}

