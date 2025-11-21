from typing import Any, Dict, List

from Products.Five.browser.pagetemplatefile import ViewPageTemplateFile
from interaktiv.kyra import _
from interaktiv.kyra.controlpanels.prompt_base import PromptManagerBaseView


class PromptManagerView(PromptManagerBaseView):
    """Plone controlpanel view for managing prompts in Kyra."""

    template = ViewPageTemplateFile('templates/prompt_manager.pt')

    def __call__(self) -> str:
        if self.request.method == 'POST':
            action = self.request.form.get('action')
            if action == 'create':
                self._create_prompt()
            elif action == 'delete':
                self._delete_prompt()

        return self.template()

    def get_prompts(self) -> List[Dict[str, Any]]:
        response = self.kyra.prompts.list(page=1, size=100)
        if 'error' in response:
            self._add_message(response['error'], 'error')
            return []

        # Add translated action labels to metadata
        replace_str = _('trans_option_action_replace')
        append_str = _('trans_option_action_append')
        prompts = response.get('prompts', [])
        for prompt in prompts:
            metadata = prompt.get('metadata', {})
            action = metadata.get('action', '')
            if action == 'replace':
                metadata['action_translation'] = replace_str
            elif action == 'append':
                metadata['action_translation'] = append_str
            else:
                metadata['action_translation'] = action

        return response.get('prompts', [])

    def _create_prompt(self) -> None:
        name = self.request.form.get('name', '').strip()
        prompt = self.request.form.get('prompt', '').strip()

        if not name or not prompt:
            self._add_message(f"{_('trans_error_missing_name_or_prompt')}", 'error')
            return

        description = self.request.form.get('description', '').strip()
        categories = self.request.form.get('categories', '').strip()
        category_list = [c.strip() for c in categories.split(',') if c.strip()]
        action = self.request.form.get('metadata_action', 'replace')

        payload = {
            'name': name,
            'description': description,
            'prompt': prompt,
            'metadata': {
                'categories': category_list or [],
                'action': action
            }
        }

        # Create prompt via API
        response = self.kyra.prompts.create(payload)

        if 'error' in response:
            self._add_message(response['error'], 'error')
            return

        # Handle optional file upload
        prompt_id = response.get('id', '')
        file_field = self.request.form.get('file_upload')
        if file_field and prompt_id:
            response = self.kyra.files.upload(prompt_id, file_field)
            if 'error' in response:
                self._add_message(response['error'], 'error')
                return

        self._add_message(_('trans_status_prompt_created'), 'info')

    def _delete_prompt(self) -> None:
        if not self.prompt_id:
            self._add_message(f"{_('trans_status_no_prompt_id')}", 'error')
            return

        response = self.kyra.prompts.delete(self.prompt_id)
        if 'error' in response:
            self._add_message(response['error'], 'error')
            return

        self._add_message(_('trans_status_prompt_deleted'), 'info')
