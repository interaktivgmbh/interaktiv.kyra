"""Client for file-related operations in Kyra API."""

from typing import Any, Dict, List, Tuple, Optional, Union

from ZPublisher.HTTPRequest import FileUpload
from interaktiv.kyra.api.base import APIBase


class Files(APIBase):
    """Provides methods to manage files attached to prompts, including
    upload, download, list, and delete operations.
    """

    def get(self, prompt_id: str) -> List[Dict[str, Any]]:
        """Retrieve list of files attached to a prompt."""
        url = f'{self.gateway_url}/{prompt_id}/files'
        response = self.request('GET', url)
        return response.get('files', [response])

    def upload(self, prompt_id: str, file_field: FileUpload) -> Dict[str, Any]:
        """Upload one or more files to a prompt."""
        files = []
        files_data = self._prepare_files(file_field)

        for file_data, filename, content_type in files_data:
            files.append(('files', (filename, file_data, content_type)))

        url = f'{self.gateway_url}/{prompt_id}/files'
        return self.request('POST', url, include_content_type=False, files=files)

    def _prepare_files(self, file_field: FileUpload) -> List[Tuple[bytes, str, str]]:
        files_data = []

        if isinstance(file_field, list):
            for single_file in file_field:
                file_info = self._get_file_info(single_file)
                if file_info:
                    files_data.append(file_info)
        else:
            file_info = self._get_file_info(file_field)
            if file_info:
                files_data.append(file_info)

        return files_data

    @staticmethod
    def _get_file_info(file_field) -> Optional[Tuple[bytes, str, str]]:
        filename = getattr(file_field, 'filename', '')
        file_data = file_field.read()
        content_type = getattr(file_field, 'headers', {}).get('content-type', 'application/octet-stream')

        if not filename or not file_data:
            return None

        return file_data, filename, content_type

    def download(self, prompt_id: str, file_id: str) -> Dict[str, Union[bytes, str]]:
        """Download a file from a prompt."""
        url = f'{self.gateway_url}/{prompt_id}/files/{file_id}/download'
        response = self.request('GET', url, get_content=True)
        return response

    def delete(self, prompt_id: str, file_id: str) -> Dict[str, Any]:
        """Delete a file from a prompt."""
        url = f'{self.gateway_url}/{prompt_id}/files/{file_id}'
        response = self.request('DELETE', url)
        return response
