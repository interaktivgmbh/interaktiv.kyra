import time
import unittest
from unittest.mock import patch, Mock

import plone.api as api
from interaktiv.kyra.api.base import APIBase
from interaktiv.kyra.registry.ai_assistant import IAIAssistantSchema
from interaktiv.kyra.registry.ai_assistant_cache import IAIAssistantCacheSchema
from interaktiv.kyra.testing import INTERAKTIV_KYRA_FUNCTIONAL_TESTING
from plone.app.testing import TEST_USER_ID, setRoles


class TestServiceBase(unittest.TestCase):
    layer = INTERAKTIV_KYRA_FUNCTIONAL_TESTING
    product_name = 'interaktiv.kyra'

    def setUp(self):
        self.app = self.layer['app']
        self.portal = self.layer['portal']
        self.request = self.layer['request']
        setRoles(self.portal, TEST_USER_ID, ['Manager', 'Site Administrator'])

    def test_get_api_credentials__success(self):
        # setup
        api.portal.set_registry_record(
            name="gateway_url", 
            interface=IAIAssistantSchema, 
            value='http://localhost:8080/api/prompts'
        )
        api.portal.set_registry_record(
            name="keycloak_realms_url", 
            interface=IAIAssistantSchema, 
            value='http://localhost:8080/realms/kyra'
        )
        api.portal.set_registry_record(
            name="keycloak_client_id", 
            interface=IAIAssistantSchema, 
            value='client_id'
        )
        api.portal.set_registry_record(
            name="keycloak_client_secret", 
            interface=IAIAssistantSchema, 
            value='client_secret'
        )

        # do it
        result = APIBase._get_api_credentials()

        # postcondition
        expected_result = (
            'http://localhost:8080/api/prompts',
            'http://localhost:8080/realms/kyra',
            'client_id', 
            'client_secret'
        )
        self.assertTupleEqual(result, expected_result)

    def test_get_api_credentials__missing_values(self):
        # setup - don't set registry values

        # do it
        result = APIBase._get_api_credentials()

        # postcondition
        self.assertTupleEqual(result, (None, None, None, None))

    def test__get_token_from_registry__token__valid_timestamp(self):
        # setup
        api.portal.set_registry_record(
            name='keycloak_token_value',
            interface=IAIAssistantCacheSchema,
            value='test_token'
        )
        api.portal.set_registry_record(
            name='keycloak_token_timestamp',
            interface=IAIAssistantCacheSchema,
            value=str(time.time())
        )

        # do it
        result = APIBase._get_token_from_registry()

        # postcondition
        self.assertEqual(result, 'test_token')

    def test__get_token_from_registry__token__no_timestamp(self):
        # setup
        api.portal.set_registry_record(
            name='keycloak_token_value',
            interface=IAIAssistantCacheSchema,
            value='test_token'
        )
        api.portal.set_registry_record(
            name='keycloak_token_timestamp',
            interface=IAIAssistantCacheSchema,
            value=''
        )

        # do it
        result = APIBase._get_token_from_registry()

        # postcondition
        self.assertEqual(result, '')

    def test__get_token_from_registry__no_token__valid_timestamp(self):
        # setup
        api.portal.set_registry_record(
            name='keycloak_token_value',
            interface=IAIAssistantCacheSchema,
            value=''
        )
        api.portal.set_registry_record(
            name='keycloak_token_timestamp',
            interface=IAIAssistantCacheSchema,
            value=str(time.time())
        )

        # do it
        result = APIBase._get_token_from_registry()

        # postcondition
        self.assertEqual(result, '')

    def test__get_token_from_registry__token__old_timestamp(self):
        # setup
        api.portal.set_registry_record(
            name='keycloak_token_value',
            interface=IAIAssistantCacheSchema,
            value='test_token'
        )
        now_timestamp = time.time()
        one_day_ago = now_timestamp - 86400
        api.portal.set_registry_record(
            name='keycloak_token_timestamp',
            interface=IAIAssistantCacheSchema,
            value=str(one_day_ago)
        )

        # do it
        result = APIBase._get_token_from_registry()

        # postcondition
        self.assertEqual(result, '')

    def test__update_token_in_registry(self):
        # do it
        APIBase._update_token_in_registry(token='test_token')

        # postcondition
        token = api.portal.get_registry_record(
            name='keycloak_token_value',
            interface=IAIAssistantCacheSchema
        )
        self.assertEqual(token, 'test_token')

        token_timestamp = api.portal.get_registry_record(
             name='keycloak_token_timestamp',
             interface=IAIAssistantCacheSchema
        )
        now_timestamp = time.time()
        self.assertTrue(float(now_timestamp) > float(token_timestamp))

    @patch('interaktiv.kyra.api.base.requests.post')
    def test_get_token__success(self, mock_post):
        # setup
        realms_url = 'http://localhost:8080/realms/kyra'
        client_id = 'client_id'
        client_secret = 'client_secret'
        mocked_token = 'mocked_access_token_12345'

        mock_response = Mock()
        mock_response.json.return_value = {'access_token': mocked_token}
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        service = APIBase()

        # do it
        result = service._get_token(realms_url, client_id, client_secret)

        # postcondition
        self.assertEqual(result, mocked_token)

        mock_post.assert_called_once_with(
            'http://localhost:8080/realms/kyra/protocol/openid-connect/token',
            data={
                'grant_type': 'client_credentials',
                'client_id': 'client_id',
                'client_secret': 'client_secret'
            }
        )

    @patch('interaktiv.kyra.api.base.requests.post')
    def test_get_token__request_error(self, mock_post):
        # setup
        mock_post.side_effect = Exception('Connection error')

        # do it & postcondition
        with self.assertRaises(Exception):
            APIBase._get_token('url', 'id', 'secret')

    @patch('interaktiv.kyra.api.base.APIBase._get_token')
    @patch('interaktiv.kyra.api.base.APIBase._get_api_credentials')
    def test_token_property__caches_token(self, mock_get_creds, mock_get_token):
        # setup
        mock_get_creds.return_value = ('url', 'realms', 'id', 'secret')
        mock_get_token.return_value = 'test_token_123'
        
        service = APIBase()

        # do it - access token twice
        token1 = service.token
        token2 = service.token

        # postcondition
        self.assertEqual(token1, 'test_token_123')
        self.assertEqual(token2, 'test_token_123')
        # Token should only be fetched once (cached)
        mock_get_token.assert_called_once()

    @patch('interaktiv.kyra.api.base.APIBase._get_token')
    @patch('interaktiv.kyra.api.base.APIBase._get_api_credentials')
    def test_gateway_url_property__success(self, mock_get_creds, mock_get_token):
        # setup
        mock_get_creds.return_value = ('http://localhost:8080/api', 'realms', 'id', 'secret')
        mock_get_token.return_value = 'test_token_123'

        service = APIBase()

        # do it
        url = service.gateway_url

        # postcondition
        self.assertEqual(url, 'http://localhost:8080/api')
