import unittest
from unittest.mock import patch, Mock

import plone.api as api
from interaktiv.kyra.registry.ai_assistant import IAIAssistantSchema
from interaktiv.kyra.api import KyraAPI
from interaktiv.kyra.testing import INTERAKTIV_KYRA_FUNCTIONAL_TESTING
from plone.app.testing import TEST_USER_ID, setRoles


class TestFiles(unittest.TestCase):
    layer = INTERAKTIV_KYRA_FUNCTIONAL_TESTING
    product_name = 'interaktiv.kyra'

    def setUp(self):
        self.app = self.layer['app']
        self.portal = self.layer['portal']
        self.request = self.layer['request']
        setRoles(self.portal, TEST_USER_ID, ['Manager', 'Site Administrator'])
        
        api.portal.set_registry_record(
            name='gateway_url',
            interface=IAIAssistantSchema, 
            value='http://localhost:8080/api/prompts'
        )
        api.portal.set_registry_record(
            name='keycloak_realms_url',
            interface=IAIAssistantSchema, 
            value='http://localhost:8080/realms/kyra'
        )
        api.portal.set_registry_record(
            name='keycloak_client_id',
            interface=IAIAssistantSchema, 
            value='test_client_id'
        )
        api.portal.set_registry_record(
            name='keycloak_client_secret',
            interface=IAIAssistantSchema, 
            value='test_client_secret'
        )
        api.portal.set_registry_record(
            name='domain_id',
            interface=IAIAssistantSchema,
            value='test-domain'
        )

    def _create_mock_file(self, filename='test.txt', content=b'test content', content_type='text/plain'):
        """Helper method to create a mock FileUpload object"""
        mock_file = Mock()
        mock_file.filename = filename
        mock_file.read.return_value = content
        mock_file.headers = {'content-type': content_type}
        return mock_file

    @patch('interaktiv.kyra.api.base.requests.post')
    @patch('interaktiv.kyra.api.base.requests.request')
    def test_get__success(self, mock_request, mock_post):
        # setup
        mock_token_response = Mock()
        mock_token_response.json.return_value = {'access_token': 'test_token_123'}
        mock_token_response.raise_for_status = Mock()
        mock_post.return_value = mock_token_response

        prompt_id = 'test-prompt-id'
        mock_files_response = Mock()
        mock_files_response.status_code = 200
        mock_files_response.headers = {'content-type': 'application/json'}
        mock_files_response.json.return_value = {
            'files': [
                {'id': 'file-1', 'filename': 'test.txt', 'size': 1024}
            ]
        }
        mock_files_response.raise_for_status = Mock()
        mock_request.return_value = mock_files_response

        kyra = KyraAPI()

        # do it
        result = kyra.files.get(prompt_id)

        # postcondition
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['filename'], 'test.txt')
        
        mock_request.assert_called_once()
        call_args = mock_request.call_args
        self.assertEqual(call_args[0][0], 'GET')  # HTTP method
        self.assertIn(prompt_id, call_args[0][1])  # URL contains prompt_id
        self.assertIn('files', call_args[0][1])  # URL contains 'files'

    @patch('interaktiv.kyra.api.base.requests.post')
    @patch('interaktiv.kyra.api.base.requests.request')
    def test_upload__success_single_file(self, mock_request, mock_post):
        # setup
        mock_token_response = Mock()
        mock_token_response.json.return_value = {'access_token': 'test_token_123'}
        mock_token_response.raise_for_status = Mock()
        mock_post.return_value = mock_token_response

        prompt_id = 'test-prompt-id'
        mock_upload_response = Mock()
        mock_upload_response.status_code = 201
        mock_upload_response.headers = {'content-type': 'application/json'}
        mock_upload_response.json.return_value = [
            {'id': 'file-1', 'filename': 'test.txt'}
        ]
        mock_upload_response.raise_for_status = Mock()
        mock_request.return_value = mock_upload_response

        mock_file = self._create_mock_file('test.txt', b'test content')

        kyra = KyraAPI()

        # do it
        result = kyra.files.upload(prompt_id, mock_file)

        # postcondition
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['filename'], 'test.txt')

        mock_request.assert_called_once()
        call_kwargs = mock_request.call_args[1]
        self.assertIn('files', call_kwargs)

    @patch('interaktiv.kyra.api.base.requests.post')
    @patch('interaktiv.kyra.api.base.requests.request')
    def test_upload__success_multiple_files(self, mock_request, mock_post):
        # setup
        mock_token_response = Mock()
        mock_token_response.json.return_value = {'access_token': 'test_token_123'}
        mock_token_response.raise_for_status = Mock()
        mock_post.return_value = mock_token_response

        prompt_id = 'test-prompt-id'
        mock_upload_response = Mock()
        mock_upload_response.status_code = 201
        mock_upload_response.headers = {'content-type': 'application/json'}
        mock_upload_response.json.return_value = [
            {'id': 'file-1', 'filename': 'test1.txt'},
            {'id': 'file-2', 'filename': 'test2.txt'}
        ]
        mock_upload_response.raise_for_status = Mock()
        mock_request.return_value = mock_upload_response

        mock_file1 = self._create_mock_file('test1.txt', b'content 1')
        mock_file2 = self._create_mock_file('test2.txt', b'content 2')

        kyra = KyraAPI()

        # do it
        result = kyra.files.upload(prompt_id, [mock_file1, mock_file2])

        # postcondition
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 2)

    @patch('interaktiv.kyra.api.base.requests.post')
    @patch('interaktiv.kyra.api.base.requests.request')
    def test_download__success(self, mock_request, mock_post):
        # setup
        mock_token_response = Mock()
        mock_token_response.json.return_value = {'access_token': 'test_token_123'}
        mock_token_response.raise_for_status = Mock()
        mock_post.return_value = mock_token_response

        prompt_id = 'test-prompt-id'
        file_id = 'file-1'
        mock_download_response = Mock()
        mock_download_response.status_code = 200
        mock_download_response.headers = {'content-type': 'text/plain'}
        mock_download_response.content = b'file content'
        mock_download_response.raise_for_status = Mock()
        mock_request.return_value = mock_download_response

        kyra = KyraAPI()

        # do it
        result = kyra.files.download(prompt_id, file_id)

        # postcondition
        self.assertIsInstance(result, dict)
        self.assertEqual(result['content'], b'file content')

    @patch('interaktiv.kyra.api.base.requests.post')
    @patch('interaktiv.kyra.api.base.requests.request')
    def test_delete__success(self, mock_request, mock_post):
        # setup
        mock_token_response = Mock()
        mock_token_response.json.return_value = {'access_token': 'test_token_123'}
        mock_token_response.raise_for_status = Mock()
        mock_post.return_value = mock_token_response

        prompt_id = 'test-prompt-id'
        file_id = 'file-1'
        mock_delete_response = Mock()
        mock_delete_response.status_code = 204
        mock_delete_response.raise_for_status = Mock()
        mock_request.return_value = mock_delete_response

        kyra = KyraAPI()

        # do it
        result = kyra.files.delete(prompt_id, file_id)

        # postcondition
        self.assertIsInstance(result, dict)
        self.assertEqual(result, {})  # 204 returns empty dict
        
        call_args = mock_request.call_args
        self.assertEqual(call_args[0][0], 'DELETE')
        self.assertIn(prompt_id, call_args[0][1])
        self.assertIn(file_id, call_args[0][1])

    @patch('interaktiv.kyra.api.base.APIBase._get_token')
    def test__prepare_files__single_file(self, mock_get_token):
        # setup
        mock_get_token.return_value = 'test-token'

        mock_file = self._create_mock_file('test.txt', b'test content')

        with patch('interaktiv.kyra.api.base.requests.post') as mock_post:
            mock_token_response = Mock()
            mock_token_response.json.return_value = {'access_token': 'test_token'}
            mock_token_response.raise_for_status = Mock()
            mock_post.return_value = mock_token_response

        kyra = KyraAPI()

        # do it
        result = kyra.files._prepare_files(mock_file)

        # postcondition
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][1], 'test.txt')  # filename
        self.assertEqual(result[0][0], b'test content')  # content

    @patch('interaktiv.kyra.api.base.APIBase._get_token')
    def test__prepare_files__multiple_files(self, mock_get_token):
        # setup
        mock_get_token.return_value = 'test-token'

        mock_file1 = self._create_mock_file('test1.txt', b'content 1')
        mock_file2 = self._create_mock_file('test2.txt', b'content 2')

        with patch('interaktiv.kyra.api.base.requests.post') as mock_post:
            mock_token_response = Mock()
            mock_token_response.json.return_value = {'access_token': 'test_token'}
            mock_token_response.raise_for_status = Mock()
            mock_post.return_value = mock_token_response

        kyra = KyraAPI()

        # do it
        result = kyra.files._prepare_files([mock_file1, mock_file2])

        # postcondition
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0][1], 'test1.txt')
        self.assertEqual(result[1][1], 'test2.txt')

    def test__prepare_files__empty_file(self):
        # setup
        mock_file = Mock()
        mock_file.filename = ''
        mock_file.read.return_value = b''
        mock_file.headers = {'content-type': 'text/plain'}

        with patch('interaktiv.kyra.api.base.requests.post') as mock_post:
            mock_token_response = Mock()
            mock_token_response.json.return_value = {'access_token': 'test_token'}
            mock_token_response.raise_for_status = Mock()
            mock_post.return_value = mock_token_response

            kyra = KyraAPI()

        # do it
        result = kyra.files._prepare_files(mock_file)

        # postcondition
        self.assertEqual(result, [])

    def test__get_file_info__valid_file(self):
        # setup
        mock_file = self._create_mock_file('test.txt', b'test content')

        with patch('interaktiv.kyra.api.base.requests.post') as mock_post:
            mock_token_response = Mock()
            mock_token_response.json.return_value = {'access_token': 'test_token'}
            mock_token_response.raise_for_status = Mock()
            mock_post.return_value = mock_token_response

            kyra = KyraAPI()

        # do it
        result = kyra.files._get_file_info(mock_file)

        # postcondition
        self.assertIsNotNone(result)
        self.assertEqual(result[0], b'test content')  # file_data
        self.assertEqual(result[1], 'test.txt')  # filename
        self.assertEqual(result[2], 'text/plain')  # content_type

    def test__get_file_info__no_filename(self):
        # setup
        mock_file = Mock()
        mock_file.filename = ''
        mock_file.read.return_value = b'test content'
        mock_file.headers = {'content-type': 'text/plain'}

        with patch('interaktiv.kyra.api.base.requests.post') as mock_post:
            mock_token_response = Mock()
            mock_token_response.json.return_value = {'access_token': 'test_token'}
            mock_token_response.raise_for_status = Mock()
            mock_post.return_value = mock_token_response

            kyra = KyraAPI()

        # do it
        result = kyra.files._get_file_info(mock_file)

        # postcondition
        self.assertIsNone(result)

    def test__get_file_info__no_content(self):
        # setup
        mock_file = Mock()
        mock_file.filename = 'test.txt'
        mock_file.read.return_value = b''
        mock_file.headers = {'content-type': 'text/plain'}

        with patch('interaktiv.kyra.api.base.requests.post') as mock_post:
            mock_token_response = Mock()
            mock_token_response.json.return_value = {'access_token': 'test_token'}
            mock_token_response.raise_for_status = Mock()
            mock_post.return_value = mock_token_response

            kyra = KyraAPI()

        # do it
        result = kyra.files._get_file_info(mock_file)

        # postcondition
        self.assertIsNone(result)
