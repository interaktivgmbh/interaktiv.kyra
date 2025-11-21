from typing import Tuple, Any, Dict

import requests
from interaktiv.kyra import logger
from interaktiv.kyra.registry.ai_assistant import IAIAssistantSchema
from interaktiv.kyra.registry.ai_assistant_cache import IAIAssistantCacheSchema
from plone import api
import time


KEYCLOAK_TOKEN_EXPIRATION_TIME_DEFAULT = 1200


class APIBase:
    gateway_url: str
    realms_url: str
    client_id: str
    client_secret: str
    token: str

    def __init__(self) -> None:
        self.gateway_url, self.realms_url, self.client_id, self.client_secret = self._get_api_credentials()
        self.token = self._get_token(self.realms_url, self.client_id, self.client_secret)

    @staticmethod
    def _get_api_credentials() -> Tuple[str, str, str, str]:
        gateway_url = api.portal.get_registry_record(
            name='gateway_url',
            interface=IAIAssistantSchema
        )
        realms_url = api.portal.get_registry_record(
            name='keycloak_realms_url',
            interface=IAIAssistantSchema
        )
        client_id = api.portal.get_registry_record(
            name='keycloak_client_id',
            interface=IAIAssistantSchema
        )
        client_secret = api.portal.get_registry_record(
            name='keycloak_client_secret',
            interface=IAIAssistantSchema
        )
        return gateway_url, realms_url, client_id, client_secret

    @staticmethod
    def _get_token_from_registry() -> str:
        token_timestamp = api.portal.get_registry_record(
             name='keycloak_token_timestamp',
             interface=IAIAssistantCacheSchema
        )
        if not token_timestamp:
            return ''

        token_expiration_time = api.portal.get_registry_record(
            name='keycloak_token_expiration_time',
            interface=IAIAssistantSchema
        ) or KEYCLOAK_TOKEN_EXPIRATION_TIME_DEFAULT

        now_timestamp = time.time()
        diff_timestamps = float(now_timestamp) - float(token_timestamp)
        if diff_timestamps > token_expiration_time:
            return ''

        token = api.portal.get_registry_record(
            name='keycloak_token_value',
            interface=IAIAssistantCacheSchema
        )
        if not token:
            return ''

        return token

    @staticmethod
    def _update_token_in_registry(token: str) -> None:
        api.portal.set_registry_record(
            name='keycloak_token_value',
            value=token,
            interface=IAIAssistantCacheSchema
        )
        api.portal.set_registry_record(
             name='keycloak_token_timestamp',
             value=str(time.time()),
             interface=IAIAssistantCacheSchema
        )

    def _get_token(self, realms_url: str, client_id: str, client_secret: str) -> str:
        if not (realms_url and client_id and client_secret):
            return ''

        token_from_registry = self._get_token_from_registry()
        if token_from_registry:
            return token_from_registry

        token_url = f'{realms_url}/protocol/openid-connect/token'

        data = {
            'grant_type': 'client_credentials',
            'client_id': client_id,
            'client_secret': client_secret,
        }

        try:
            response = requests.post(token_url, data=data)
            response.raise_for_status()

            token_data = response.json()
            token = token_data.get('access_token', '')

            self._update_token_in_registry(token)
            return token

        except requests.HTTPError:
            return ''

    def request(
            self,
            method: str,
            url: str,
            include_content_type: bool = True,
            get_content: bool = False,
            **kwargs
    ) -> Dict[str, Any]:
        headers = self._get_headers(include_content_type)
        if not headers:
            return {'error': 'No headers available'}

        try:
            response = requests.request(method, url, headers=headers, timeout=30, **kwargs)
            response.raise_for_status()

            if response.status_code in (200, 201) and hasattr(response, 'content'):
                if 'application/json' in response.headers.get('content-type'):
                    return response.json()
                elif get_content:
                    return {'content': response.content}
            elif response.status_code == 204:
                return {}

            reason = getattr(response, 'reason', 'Request failed')
            return {'error': reason}

        except requests.HTTPError as e:
            logger.error(f'API HTTP error: {e}')
            if e.response is not None:
                try:
                    error_detail = e.response.json()
                    error_msg = error_detail.get('error', str(e))
                    logger.error(f'API error detail: {error_detail}')
                    return {'error': error_msg}
                except Exception:
                    return {'error': str(e)}
            return {'error': str(e)}

        except requests.Timeout:
            logger.error('API request timeout')
            return {'error': 'Request timeout - please try again'}

        except requests.ConnectionError:
            logger.error('API connection error')
            return {'error': 'Cannot connect to API service'}

        except Exception as e:
            logger.error(f'API request failed: {e}')
            return {'error': f'Request failed: {e}'}

    def _get_headers(self, include_content_type: bool = True) -> Dict[str, str]:
        domain_id = self._get_domain_id()
        if not (self.token and domain_id):
            return {}

        headers = {
            'Authorization': f'Bearer {self.token}',
            'x-domain-id': domain_id
        }
        if include_content_type:
            headers['Content-Type'] = 'application/json'
        return headers

    @staticmethod
    def _get_domain_id() -> str:
        domain_id = api.portal.get_registry_record(
            name='domain_id',
            interface=IAIAssistantSchema
        )
        return domain_id or 'plone'
