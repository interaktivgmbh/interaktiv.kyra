import unittest
from unittest.mock import patch, Mock

from interaktiv.kyra.controlpanels.prompt_edit import PromptEditView
from interaktiv.kyra.testing import INTERAKTIV_KYRA_FUNCTIONAL_TESTING
from plone.app.testing import TEST_USER_ID, setRoles


class TestPromptEditView(unittest.TestCase):
    layer = INTERAKTIV_KYRA_FUNCTIONAL_TESTING
    product_name = 'interaktiv.kyra'

    def setUp(self):
        self.app = self.layer['app']
        self.portal = self.layer['portal']
        self.request = self.layer['request']
        setRoles(self.portal, TEST_USER_ID, ['Manager', 'Site Administrator'])

    def _create_view(self, form_data=None):
        if form_data:
            self.request.form.update(form_data)
        view = PromptEditView(self.portal, self.request)
        return view

    @patch('interaktiv.kyra.api.base.APIBase._get_token')
    @patch('interaktiv.kyra.api.prompts.Prompts.get')
    def test_get_prompt__with_prompt_id(self, mock_get_prompt, mock_get_token):
        # setup
        mock_get_token.return_value = 'test-token'
        mock_get_prompt.return_value = {
            'id': 'test-id',
            'name': 'Test Prompt',
            'prompt': 'Test content'
        }
        form_data = {'prompt_id': 'test-id'}
        view = self._create_view(form_data)

        # do it
        result = view.get_prompt()

        # postcondition
        self.assertEqual(result['id'], 'test-id')
        mock_get_prompt.assert_called_once_with('test-id')

    @patch('interaktiv.kyra.api.prompts.Prompts.get')
    def test_get_prompt__no_prompt_id(self, mock_get_prompt):
        # setup
        view = self._create_view()

        # do it
        result = view.get_prompt()

        # postcondition
        self.assertIsInstance(result, dict)
        self.assertEqual(result, {})
        mock_get_prompt.assert_not_called()

    @patch('interaktiv.kyra.api.prompts.Prompts.get')
    def test_get_prompt__api_error(self, mock_get_prompt):
        # setup
        mock_get_prompt.return_value = {'error': 'Not found'}
        form_data = {'prompt_id': 'test-id'}
        view = self._create_view(form_data)

        # do it
        result = view.get_prompt()

        # postcondition
        self.assertIsInstance(result, dict)
        self.assertEqual(result, {})

    @patch('interaktiv.kyra.api.files.Files.get')
    def test_get_files__with_prompt_id(self, mock_get_files):
        # setup
        mock_get_files.return_value = [
            {'id': 'file-1', 'filename': 'test.txt'}
        ]
        form_data = {'prompt_id': 'test-id'}
        view = self._create_view(form_data)

        # do it
        result = view.get_files()

        # postcondition
        self.assertEqual(len(result), 1)
        mock_get_files.assert_called_once_with('test-id')

    @patch('interaktiv.kyra.api.files.Files.get')
    def test_get_files__no_prompt_id(self, mock_get_files):
        # setup
        view = self._create_view()

        # do it
        result = view.get_files()

        # postcondition
        self.assertEqual(result, [])
        mock_get_files.assert_not_called()

    @patch('interaktiv.kyra.api.files.Files.get')
    def test_get_files__empty_files(self, mock_get_files):
        # setup
        mock_get_files.return_value = []
        form_data = {'prompt_id': 'test-id'}
        view = self._create_view(form_data)

        # do it
        result = view.get_files()

        # postcondition
        self.assertEqual(result, [])

    @patch('interaktiv.kyra.api.files.Files.get')
    def test_get_files__api_error(self, mock_get_files):
        # setup
        mock_get_files.return_value = [{'error': 'API Error'}]
        form_data = {'prompt_id': 'test-id'}
        view = self._create_view(form_data)

        # do it
        result = view.get_files()

        # postcondition
        self.assertIsInstance(result, list)
        self.assertEqual(result, [])

    @patch('interaktiv.kyra.controlpanels.prompt_base.IStatusMessage')
    @patch('interaktiv.kyra.api.prompts.Prompts.update')
    def test__update_prompt__success_without_files(self, mock_update, mock_status_message):
        # setup
        mock_status = Mock()
        mock_status_message.return_value = mock_status
        form_data = {
            'prompt_id': 'test-id',
            'name': 'Updated Name',
            'prompt': 'Updated prompt',
            'description': '',
            'categories': '',
            'metadata_action': 'replace'
        }
        mock_update.return_value = {'id': 'test-id'}
        view = self._create_view(form_data)

        # do it
        view._update_prompt()

        # postcondition
        mock_update.assert_called_once()
        mock_status.addStatusMessage.assert_called_once_with(
            'trans_status_prompt_updated',
            type='info'
        )

    @patch('interaktiv.kyra.controlpanels.prompt_base.IStatusMessage')
    @patch('interaktiv.kyra.api.files.Files.upload')
    @patch('interaktiv.kyra.api.prompts.Prompts.update')
    def test__update_prompt__success_with_files(self, mock_update, mock_upload, mock_status_message):
        # setup
        mock_status = Mock()
        mock_status_message.return_value = mock_status
        mock_file = Mock()
        form_data = {
            'prompt_id': 'test-id',
            'name': 'Updated Name',
            'prompt': 'Updated prompt',
            'description': '',
            'categories': '',
            'metadata_action': 'replace',
            'file_upload': mock_file
        }
        mock_update.return_value = {'id': 'test-id'}
        mock_upload.return_value = {'success': True}
        view = self._create_view(form_data)

        # do it
        view._update_prompt()

        # postcondition
        mock_upload.assert_called_once()
        mock_status.addStatusMessage.assert_called_once_with(
            'trans_status_prompt_updated',
            type='info'
        )

    @patch('interaktiv.kyra.controlpanels.prompt_base.IStatusMessage')
    @patch('interaktiv.kyra.api.prompts.Prompts.update')
    def test__update_prompt__api_error(self, mock_update, mock_status_message):
        # setup
        mock_status = Mock()
        mock_status_message.return_value = mock_status
        form_data = {
            'prompt_id': 'test-id',
            'name': 'Updated Name',
            'prompt': 'Updated prompt',
            'description': '',
            'categories': '',
            'metadata_action': 'replace'
        }
        mock_update.return_value = {'error': 'Update failed'}
        view = self._create_view(form_data)

        # do it
        view._update_prompt()

        # postcondition
        mock_status.addStatusMessage.assert_called_once_with(
            'Update failed',
            type='error'
        )

    @patch('interaktiv.kyra.controlpanels.prompt_base.IStatusMessage')
    @patch('interaktiv.kyra.api.files.Files.upload')
    @patch('interaktiv.kyra.api.prompts.Prompts.update')
    def test__update_prompt__file_upload_error(self, mock_update, mock_upload, mock_status_message):
        # setup
        mock_status = Mock()
        mock_status_message.return_value = mock_status
        mock_file = Mock()
        form_data = {
            'prompt_id': 'test-id',
            'name': 'Updated Name',
            'prompt': 'Updated prompt',
            'description': '',
            'categories': '',
            'metadata_action': 'replace',
            'file_upload': mock_file
        }
        mock_update.return_value = {'id': 'test-id'}
        mock_upload.return_value = {'error': 'Upload failed'}
        view = self._create_view(form_data)

        # do it
        view._update_prompt()

        # postcondition
        mock_status.addStatusMessage.assert_called_once_with(
            'Upload failed',
            type='error'
        )

    @patch('interaktiv.kyra.controlpanels.prompt_base.IStatusMessage')
    @patch('interaktiv.kyra.api.files.Files.delete')
    def test__delete_file__success(self, mock_delete, mock_status_message):
        # setup
        mock_status = Mock()
        mock_status_message.return_value = mock_status
        form_data = {
            'prompt_id': 'test-id',
            'file_id': 'file-1'
        }
        mock_delete.return_value = {'success': True}
        view = self._create_view(form_data)

        # do it
        view._delete_file()

        # postcondition
        mock_delete.assert_called_once_with('test-id', 'file-1')
        mock_status.addStatusMessage.assert_called_once_with(
            'trans_status_file_deleted',
            type='info'
        )

    @patch('interaktiv.kyra.controlpanels.prompt_base.IStatusMessage')
    @patch('interaktiv.kyra.api.files.Files.delete')
    def test__delete_file__no_file_id(self, mock_delete, mock_status_message):
        # setup
        mock_status = Mock()
        mock_status_message.return_value = mock_status
        form_data = {'prompt_id': 'test-id'}
        view = self._create_view(form_data)

        # do it
        view._delete_file()

        # postcondition
        mock_delete.assert_not_called()
        mock_status.addStatusMessage.assert_called_once_with(
            'trans_error_missing_ids',
            type='error'
        )

    @patch('interaktiv.kyra.controlpanels.prompt_base.IStatusMessage')
    @patch('interaktiv.kyra.api.files.Files.delete')
    def test__delete_file__api_error(self, mock_delete, mock_status_message):
        # setup
        mock_status = Mock()
        mock_status_message.return_value = mock_status
        form_data = {
            'prompt_id': 'test-id',
            'file_id': 'file-1'
        }
        mock_delete.return_value = {'error': 'Delete failed'}
        view = self._create_view(form_data)

        # do it
        view._delete_file()

        # postcondition
        mock_status.addStatusMessage.assert_called_once_with(
            'Delete failed',
            type='error'
        )
