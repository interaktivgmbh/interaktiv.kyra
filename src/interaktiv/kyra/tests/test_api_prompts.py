import unittest
from unittest.mock import patch

import plone.api as api
from interaktiv.kyra.registry.ai_assistant import IAIAssistantSchema
from interaktiv.kyra.api import KyraAPI
from interaktiv.kyra.testing import INTERAKTIV_KYRA_FUNCTIONAL_TESTING
from plone.app.testing import TEST_USER_ID, setRoles


class TestPromptsService(unittest.TestCase):
    layer = INTERAKTIV_KYRA_FUNCTIONAL_TESTING
    product_name = 'interaktiv.kyra'

    def setUp(self):
        self.app = self.layer['app']
        self.portal = self.layer['portal']
        self.request = self.layer['request']
        setRoles(self.portal, TEST_USER_ID, ['Manager', 'Site Administrator'])
        
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
            value='test_client_id'
        )
        api.portal.set_registry_record(
            name="keycloak_client_secret", 
            interface=IAIAssistantSchema, 
            value='test_client_secret'
        )

    @patch('interaktiv.kyra.api.base.APIBase._get_token')
    @patch('interaktiv.kyra.api.base.APIBase.request')
    def test_list__success(self, mock_request, mock_get_token):
        # setup
        mock_get_token.return_value = 'test-token'
        mock_request.return_value = {
            'prompts': [{'id': 'test-1', 'name': 'Test Prompt'}],
            'total': 1
        }

        kyra = KyraAPI()

        # do it
        result = kyra.prompts.list()

        # postcondition
        self.assertIsInstance(result, dict)
        self.assertEqual(result['total'], 1)
        self.assertEqual(len(result['prompts']), 1)

        mock_request.assert_called_once_with(
            'GET',
            'http://localhost:8080/api/prompts',
            params={'page': 1, 'size': 100}
        )

    @patch('interaktiv.kyra.api.base.APIBase._get_token')
    @patch('interaktiv.kyra.api.base.APIBase.request')
    def test_list__with_custom_pagination(self, mock_request, mock_get_token):
        # setup
        mock_get_token.return_value = 'test-token'
        mock_request.return_value = {
            'prompts': [{'id': 'test-1'}],
            'total': 10
        }

        kyra = KyraAPI()

        # do it
        result = kyra.prompts.list(page=2, size=5)

        # postcondition
        self.assertIsInstance(result, dict)

        mock_request.assert_called_once_with(
            'GET',
            'http://localhost:8080/api/prompts',
            params={'page': 2, 'size': 5}
        )

    @patch('interaktiv.kyra.api.base.APIBase._get_token')
    @patch('interaktiv.kyra.api.base.APIBase.request')
    def test_get__success(self, mock_request, mock_get_token):
        # setup
        prompt_id = 'test-prompt-id'
        mock_get_token.return_value = 'test-token'
        mock_request.return_value = {
            'id': prompt_id,
            'name': 'Test Prompt',
            'prompt': 'Test content'
        }

        kyra = KyraAPI()

        # do it
        result = kyra.prompts.get(prompt_id)

        # postcondition
        self.assertIsInstance(result, dict)
        self.assertEqual(result['id'], prompt_id)
        self.assertEqual(result['name'], 'Test Prompt')
        
        mock_request.assert_called_once_with(
            'GET',
            f'http://localhost:8080/api/prompts/{prompt_id}'
        )

    @patch('interaktiv.kyra.api.base.APIBase._get_token')
    @patch('interaktiv.kyra.api.base.APIBase.request')
    def test_create__success_minimal(self, mock_request, mock_get_token):
        # setup
        mock_get_token.return_value = 'test-token'
        mock_request.return_value = {
            'id': 'new-prompt-id',
            'name': 'New Prompt',
            'prompt': 'Test prompt'
        }

        kyra = KyraAPI()

        payload = {
            'name': 'New Prompt',
            'prompt': 'Test prompt'
        }

        # do it
        result = kyra.prompts.create(payload)

        # postcondition
        self.assertIsInstance(result, dict)
        self.assertEqual(result['id'], 'new-prompt-id')
        
        mock_request.assert_called_once_with(
            'POST',
            'http://localhost:8080/api/prompts',
            json=payload
        )

    @patch('interaktiv.kyra.api.base.APIBase._get_token')
    @patch('interaktiv.kyra.api.base.APIBase.request')
    def test_create__success_with_all_fields(self, mock_request, mock_get_token):
        # setup
        mock_get_token.return_value = 'test-token'
        mock_request.return_value = {
            'id': 'new-prompt-id',
            'name': 'New Prompt',
            'prompt': 'Test prompt',
            'description': 'Test description',
            'categories': ['cat1', 'cat2']
        }

        kyra = KyraAPI()

        payload = {
            'name': 'New Prompt',
            'prompt': 'Test prompt',
            'description': 'Test description',
            'categories': ['cat1', 'cat2']
        }

        # do it
        result = kyra.prompts.create(payload)

        # postcondition
        self.assertIsInstance(result, dict)

        mock_request.assert_called_once_with(
            'POST',
            'http://localhost:8080/api/prompts',
            json=payload
        )

    @patch('interaktiv.kyra.api.base.APIBase._get_token')
    @patch('interaktiv.kyra.api.base.APIBase.request')
    def test_update_prompt__success(self, mock_request, mock_get_token):
        # setup
        prompt_id = 'test-prompt-id'
        mock_get_token.return_value = 'test-token'
        mock_request.return_value = {
            'id': prompt_id,
            'name': 'Updated Prompt',
            'prompt': 'Updated content'
        }

        kyra = KyraAPI()

        payload = {
            'name': 'Updated Prompt',
            'prompt': 'Updated content'
        }

        # do it
        result = kyra.prompts.update(prompt_id, payload)

        # postcondition
        self.assertIsInstance(result, dict)
        self.assertEqual(result['name'], 'Updated Prompt')
        
        mock_request.assert_called_once_with(
            'PATCH',
            f'http://localhost:8080/api/prompts/{prompt_id}',
            json=payload
        )

    @patch('interaktiv.kyra.api.base.APIBase._get_token')
    @patch('interaktiv.kyra.api.base.APIBase.request')
    def test_delete__success(self, mock_request, mock_get_token):
        # setup
        prompt_id = 'test-prompt-id'
        mock_get_token.return_value = 'test-token'
        mock_request.return_value = {'success': True}

        kyra = KyraAPI()

        # do it
        result = kyra.prompts.delete(prompt_id)

        # postcondition
        self.assertIsInstance(result, dict)
        self.assertEqual(result['success'], True)
        
        mock_request.assert_called_once_with(
            'DELETE',
            f'http://localhost:8080/api/prompts/{prompt_id}'
        )

    @patch('interaktiv.kyra.api.base.APIBase._get_token')
    @patch('interaktiv.kyra.api.base.APIBase.request')
    def test_apply__success(self, mock_request, mock_get_token):
        # setup
        prompt_id = 'test-prompt-id'
        mock_get_token.return_value = 'test-token'
        mock_request.return_value = {
            'response': 'Processed text',
            'prompt_id': prompt_id
        }

        kyra = KyraAPI()

        payload = {
            'text': 'Input text'
        }

        # do it
        result = kyra.prompts.apply(prompt_id, payload)

        # postcondition
        self.assertIsInstance(result, dict)
        self.assertEqual(result['response'], 'Processed text')
        
        mock_request.assert_called_once_with(
            'POST',
            f'http://localhost:8080/api/prompts/{prompt_id}/apply',
            json=payload
        )
