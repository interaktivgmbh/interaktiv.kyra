from datetime import datetime
from typing import Any, Dict, List, Union

from Products.Five.browser.pagetemplatefile import ViewPageTemplateFile
from interaktiv.kyra import _
from interaktiv.kyra.controlpanels.prompt_base import PromptManagerBaseView
from plone import api


class PromptEditView(PromptManagerBaseView):
    template = ViewPageTemplateFile('templates/prompt_edit.pt')

    def __call__(self) -> Union[str, bytes]:
        if self.request.method == 'POST':
            action = self.request.form.get('action', 'update')
            if action == 'update':
                self._update_prompt()
            elif action == 'download_file':
                return self._download_file()
            elif action == 'delete_file':
                self._delete_file()

        return self.template()

    def get_prompt(self) -> Dict[str, Any]:
        if not self.prompt_id:
            self._add_message(f"{_('trans_status_no_prompt_id')}", 'error')
            return {}

        response = self.kyra.prompts.get(self.prompt_id)
        if 'error' in response:
            self._add_message(response['error'], 'error')
            return {}

        return response

    def get_files(self) -> List[Dict[str, Any]]:
        if not self.prompt_id:
            self._add_message(f"{_('trans_status_no_prompt_id')}", 'error')
            return []

        response = self.kyra.files.get(self.prompt_id)
        if not response:
            return []

        if 'error' in response[0]:
            self._add_message(response[0]['error'], 'error')
            return []

        unknown_str = _('trans_unknown')
        for file in response:
            if not file.get('filename', ''):
                file['filename'] = unknown_str

            file['upload_date'] = unknown_str
            created_at = file.get('createdAt', '')
            if created_at:
                dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                file['upload_date'] = dt.strftime('%d.%m.%Y')

            file['size_formatted'] = unknown_str
            size_bytes = file.get('sizeBytes', 0)
            if size_bytes:
                size_mb = size_bytes / (1024 * 1024)
                file['size_formatted'] = f'{size_mb:.2f} MB'

        return response

    def _update_prompt(self) -> None:
        if not self.prompt_id:
            self._add_message(f"{_('trans_status_no_prompt_id')}", 'error')
            return

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

        response = self.kyra.prompts.update(self.prompt_id, payload)

        if 'error' in response:
            self._add_message(response['error'], 'error')
            return

        file_field = self.request.form.get('file_upload')
        if file_field:
            response = self.kyra.files.upload(self.prompt_id, file_field)
            if 'error' in response:
                self._add_message(response['error'], 'error')
                return

        self._add_message(_('trans_status_prompt_updated'), 'info')

        portal_url = api.portal.get().absolute_url()
        self.request.response.redirect(f'{portal_url}/@@ai-prompt-manager')

    def _download_file(self) -> bytes:
        file_id = self.request.form.get('file_id')
        filename = self.request.form.get('filename', 'download')

        if not self.prompt_id or not file_id:
            self._add_message(f"{_('trans_error_missing_ids')}", 'error')
            return b''

        response = self.kyra.files.download(self.prompt_id, file_id)

        if 'error' in response:
            self._add_message(response['error'], 'error')
            return b''

        self.request.response.setHeader('Content-Type', 'application/octet-stream')
        self.request.response.setHeader('Content-Disposition', f'attachment; filename="{filename}"')

        return response.get('content', b'')

    def _delete_file(self) -> None:
        file_id = self.request.form.get('file_id')

        if not self.prompt_id or not file_id:
            self._add_message(f"{_('trans_error_missing_ids')}", 'error')
            return

        response = self.kyra.files.delete(self.prompt_id, file_id)
        if 'error' in response:
            self._add_message(response['error'], 'error')
            return

        self._add_message(_('trans_status_file_deleted'), 'info')

        portal_url = api.portal.get().absolute_url()
        self.request.response.redirect(f'{portal_url}/@@ai-prompt-edit?prompt_id={self.prompt_id}')
