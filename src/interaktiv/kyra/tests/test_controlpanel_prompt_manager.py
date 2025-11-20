import unittest
from unittest.mock import patch, Mock

from interaktiv.kyra.controlpanels.prompt_manager import PromptManagerView
from interaktiv.kyra.testing import INTERAKTIV_KYRA_FUNCTIONAL_TESTING
from plone.app.testing import TEST_USER_ID, setRoles


class TestPromptManagerView(unittest.TestCase):
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
        view = PromptManagerView(self.portal, self.request)
        return view

    @patch('interaktiv.kyra.api.prompts.Prompts.list')
    def test_get_prompts__success(self, mock_get_prompts):
        # setup
        mock_get_prompts.return_value = {
            'prompts': [{'id': 'test-1', 'name': 'Test Prompt'}],
            'total': 1
        }
        view = self._create_view()

        # do it
        result = view.get_prompts()

        # postcondition
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 1)
        mock_get_prompts.assert_called_once()

    @patch('interaktiv.kyra.api.prompts.Prompts.list')
    def test_get_prompts__empty_prompts(self, mock_get_prompts):
        # setup
        mock_get_prompts.return_value = {'prompts': [], 'total': 0}
        view = self._create_view()

        # do it
        result = view.get_prompts()

        # postcondition
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 0)

    @patch('interaktiv.kyra.api.prompts.Prompts.list')
    def test_get_prompts__error_response(self, mock_get_prompts):
        # setup
        mock_get_prompts.return_value = {'error': 'API Error'}
        view = self._create_view()

        # do it
        result = view.get_prompts()

        # postcondition
        self.assertEqual(result, [])

    @patch('interaktiv.kyra.controlpanels.prompt_base.IStatusMessage')
    @patch('interaktiv.kyra.api.prompts.Prompts.create')
    def test__create_prompt__success_without_files(self, mock_create, mock_status_message):
        # setup
        mock_status = Mock()
        mock_status_message.return_value = mock_status
        form_data = {
            'name': 'New Prompt',
            'prompt': 'Test prompt',
            'description': '',
            'categories': '',
            'metadata_action': 'replace'
        }
        mock_create.return_value = {'id': 'new-prompt-id'}
        view = self._create_view(form_data)

        # do it
        view._create_prompt()

        # postcondition
        mock_create.assert_called_once()
        mock_status.addStatusMessage.assert_called_once_with(
            'trans_status_prompt_created',
            type='info'
        )

    @patch('interaktiv.kyra.controlpanels.prompt_base.IStatusMessage')
    @patch('interaktiv.kyra.api.files.Files.upload')
    @patch('interaktiv.kyra.api.prompts.Prompts.create')
    def test__create_prompt__success_with_files(self, mock_create, mock_upload, mock_status_message):
        # setup
        mock_status = Mock()
        mock_status_message.return_value = mock_status
        mock_file = Mock()
        form_data = {
            'name': 'New Prompt',
            'prompt': 'Test prompt',
            'description': '',
            'categories': '',
            'metadata_action': 'replace',
            'file_upload': mock_file
        }
        mock_create.return_value = {'id': 'new-prompt-id'}
        mock_upload.return_value = {'success': True}
        view = self._create_view(form_data)

        # do it
        view._create_prompt()

        # postcondition
        mock_upload.assert_called_once()
        mock_status.addStatusMessage.assert_called_once_with(
            'trans_status_prompt_created',
            type='info'
        )

    @patch('interaktiv.kyra.controlpanels.prompt_base.IStatusMessage')
    @patch('interaktiv.kyra.api.prompts.Prompts.create')
    def test__create_prompt__missing_name(self, mock_create, mock_status_message):
        # setup
        mock_status = Mock()
        mock_status_message.return_value = mock_status
        form_data = {
            'name': '',
            'prompt': 'Test prompt',
            'description': '',
            'categories': '',
            'metadata_action': 'replace'
        }
        view = self._create_view(form_data)

        # do it
        view._create_prompt()

        # postcondition
        mock_create.assert_not_called()
        mock_status.addStatusMessage.assert_called_once_with(
            'trans_error_missing_name_or_prompt',
            type='error'
        )

    @patch('interaktiv.kyra.controlpanels.prompt_base.IStatusMessage')
    @patch('interaktiv.kyra.api.prompts.Prompts.create')
    def test__create_prompt__missing_prompt(self, mock_create, mock_status_message):
        # setup
        mock_status = Mock()
        mock_status_message.return_value = mock_status
        form_data = {
            'name': 'Test Name',
            'prompt': '',
            'description': '',
            'categories': '',
            'metadata_action': 'replace'
        }
        view = self._create_view(form_data)

        # do it
        view._create_prompt()

        # postcondition
        mock_create.assert_not_called()
        mock_status.addStatusMessage.assert_called_once_with(
            'trans_error_missing_name_or_prompt',
            type='error'
        )

    @patch('interaktiv.kyra.controlpanels.prompt_base.IStatusMessage')
    @patch('interaktiv.kyra.api.prompts.Prompts.create')
    def test__create_prompt__api_error(self, mock_create, mock_status_message):
        # setup
        mock_status = Mock()
        mock_status_message.return_value = mock_status
        form_data = {
            'name': 'New Prompt',
            'prompt': 'Test prompt',
            'description': '',
            'categories': '',
            'metadata_action': 'replace'
        }
        mock_create.return_value = {'error': 'API Error'}
        view = self._create_view(form_data)

        # do it
        view._create_prompt()

        # postcondition
        mock_create.assert_called_once()
        mock_status.addStatusMessage.assert_called_once_with(
            'API Error',
            type='error'
        )

    @patch('interaktiv.kyra.controlpanels.prompt_base.IStatusMessage')
    @patch('interaktiv.kyra.api.files.Files.upload')
    @patch('interaktiv.kyra.api.prompts.Prompts.create')
    def test__create_prompt__file_upload_error(self, mock_create, mock_upload, mock_status_message):
        # setup
        mock_status = Mock()
        mock_status_message.return_value = mock_status
        mock_file = Mock()
        form_data = {
            'name': 'New Prompt',
            'prompt': 'Test prompt',
            'description': '',
            'categories': '',
            'metadata_action': 'replace',
            'file_upload': mock_file
        }
        mock_create.return_value = {'id': 'new-prompt-id'}
        mock_upload.return_value = {'error': 'Upload failed'}
        view = self._create_view(form_data)

        # do it
        view._create_prompt()

        # postcondition
        mock_upload.assert_called_once()
        mock_status.addStatusMessage.assert_called_once_with(
            'Upload failed',
            type='error'
        )

    @patch('interaktiv.kyra.controlpanels.prompt_base.IStatusMessage')
    @patch('interaktiv.kyra.api.prompts.Prompts.delete')
    def test__delete_prompt__with_prompt_id(self, mock_delete, mock_status_message):
        # setup
        mock_status = Mock()
        mock_status_message.return_value = mock_status
        form_data = {'prompt_id': 'test-prompt-id'}
        mock_delete.return_value = {'success': True}
        view = self._create_view(form_data)

        # do it
        view._delete_prompt()

        # postcondition
        mock_delete.assert_called_once_with('test-prompt-id')
        mock_status.addStatusMessage.assert_called_once_with(
            'trans_status_prompt_deleted',
            type='info'
        )

    @patch('interaktiv.kyra.controlpanels.prompt_base.IStatusMessage')
    @patch('interaktiv.kyra.api.prompts.Prompts.delete')
    def test__delete_prompt__no_prompt_id(self, mock_delete, mock_status_message):
        # setup
        mock_status = Mock()
        mock_status_message.return_value = mock_status
        form_data = {}
        view = self._create_view(form_data)

        # do it
        view._delete_prompt()

        # postcondition
        mock_delete.assert_not_called()
        mock_status.addStatusMessage.assert_called_once_with(
            'trans_status_no_prompt_id',
            type='error'
        )

    @patch('interaktiv.kyra.controlpanels.prompt_base.IStatusMessage')
    @patch('interaktiv.kyra.api.prompts.Prompts.delete')
    def test__delete_prompt__api_error(self, mock_delete, mock_status_message):
        # setup
        mock_status = Mock()
        mock_status_message.return_value = mock_status
        form_data = {'prompt_id': 'test-prompt-id'}
        mock_delete.return_value = {'error': 'Delete failed'}
        view = self._create_view(form_data)

        # do it
        view._delete_prompt()

        # postcondition
        mock_status.addStatusMessage.assert_called_once_with(
            'Delete failed',
            type='error'
        )

    @patch('interaktiv.kyra.controlpanels.prompt_base.IStatusMessage')
    @patch('interaktiv.kyra.controlpanels.prompt_manager.PromptManagerView._create_prompt')
    def test__call__post_create_action(self, mock_create, mock_status_message):
        # setup
        mock_status = Mock()
        mock_status_message.return_value = mock_status
        form_data = {'action': 'create'}
        view = self._create_view(form_data)
        view.request.method = 'POST'

        # do it
        with patch.object(view, 'template') as mock_template:
            mock_template.return_value = 'rendered'
            result = view()

        # postcondition
        mock_create.assert_called_once()
        self.assertEqual(result, 'rendered')
