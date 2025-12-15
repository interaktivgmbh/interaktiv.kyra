import base64
import mimetypes
from typing import Any, Dict, List

from interaktiv.kyra.services.base import ServiceBase
from zExceptions import BadRequest

from zope.interface import implementer
from zope.publisher.interfaces import IPublishTraverse


def _serialize_file(file_data: Dict[str, Any]) -> Dict[str, Any]:
    filename = file_data.get("filename") or file_data.get("name") or ""
    guessed_type, _ = mimetypes.guess_type(filename)

    raw_size = (
        file_data.get("size")
        or file_data.get("length")
        or file_data.get("fileSize")
        or file_data.get("filesize")
        or file_data.get("content_length")
        or file_data.get("contentLength")
        or file_data.get("sizeInBytes")
        or file_data.get("sizeInByte")
        or file_data.get("size_bytes")
        or file_data.get("byteSize")
        or file_data.get("bytes")
    )
    try:
        size = int(raw_size) if raw_size is not None else None
    except (TypeError, ValueError):
        size = None

    content_type = (
        file_data.get("content_type")
        or file_data.get("contentType")
        or file_data.get("mimetype")
        or file_data.get("content-type")
        or file_data.get("type")
        or guessed_type
        or "application/octet-stream"
    )

    return {
        "id": file_data.get("id") or file_data.get("_id"),
        "filename": filename,
        "size": size,
        "created": (
            file_data.get("created")
            or file_data.get("createdAt")
            or file_data.get("created_at")
        ),
        "content_type": content_type,
    }


def _ensure_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _normalize_files_response(response: Any) -> List[Dict[str, Any]]:
    if isinstance(response, list):
        if (
            len(response) == 1
            and isinstance(response[0], dict)
            and response[0].get("error")
        ):
            raise BadRequest(response[0].get("error"))
        return response

    if isinstance(response, dict):
        if response.get("error"):
            raise BadRequest(response.get("error"))
        return response.get("files") or response.get("items") or []

    return []


@implementer(IPublishTraverse)
class PromptFilesService(ServiceBase):
    """Manage prompt files via the Kyra API."""

    def __init__(self, context, request):
        super().__init__(context, request)
        self.prompt_id = None
        self.file_id = None

    def publishTraverse(self, request, name):
        if self.prompt_id is None:
            self.prompt_id = name
        else:
            self.file_id = name
        return self

    def reply(self):
        method = self.request["REQUEST_METHOD"]

        if method == "GET":
            if self.file_id:
                return self.get_file()
            return self.list_files()

        if method == "POST":
            return self.upload_files()

        if method == "DELETE":
            return self.delete_file()

        raise BadRequest("Unsupported HTTP method")

    def list_files(self):
        response = self.kyra.files.get(self.prompt_id)
        files = _normalize_files_response(response)

        return {
            "promptId": self.prompt_id,
            "files": [_serialize_file(f) for f in files],
        }

    def _get_file_meta(self) -> Dict[str, Any]:
        response = self.kyra.files.get(self.prompt_id)
        files = _normalize_files_response(response)

        for f in files:
            if f.get("id") == self.file_id or f.get("_id") == self.file_id:
                return _serialize_file(f)

        raise BadRequest(f"File '{self.file_id}' not found")

    def get_file(self):
        meta = self._get_file_meta()

        download = self.kyra.files.download(self.prompt_id, self.file_id)
        if isinstance(download, dict) and download.get("error"):
            raise BadRequest(download.get("error"))

        raw = b""
        if isinstance(download, dict):
            raw = download.get("content") or b""
        elif isinstance(download, bytes):
            raw = download

        b64 = base64.b64encode(raw).decode("ascii") if raw else ""
        if meta.get("size") in (None, 0) and raw:
            meta["size"] = len(raw)

        return {
            "promptId": self.prompt_id,
            "id": self.file_id,
            **meta,
            "data": b64,
        }

    def upload_files(self):
        files = None
        if hasattr(self.request, "form"):
            files = self.request.form.get("file")
        if files is None and "file" in self.request:
            files = self.request["file"]
        if files is None:
            raise BadRequest("No file provided")

        files = _ensure_list(files)

        uploaded = self.kyra.files.upload(self.prompt_id, files)
        if isinstance(uploaded, dict) and uploaded.get("error"):
            raise BadRequest(uploaded.get("error"))

        items = uploaded if isinstance(uploaded, list) else uploaded.get("files", [])

        return {
            "promptId": self.prompt_id,
            "uploaded": [_serialize_file(f) for f in items],
        }

    def delete_file(self):
        if not self.file_id:
            raise BadRequest("Missing file ID")

        deleted = self.kyra.files.delete(self.prompt_id, self.file_id)
        if isinstance(deleted, dict) and deleted.get("error"):
            raise BadRequest(deleted.get("error"))

        return {"promptId": self.prompt_id, "deleted": self.file_id}


@implementer(IPublishTraverse)
class PromptFileDownloadService(ServiceBase):
    """
    GET /@ai-prompt-files-download/{prompt_id}/{file_id}
    """

    def __init__(self, context, request):
        super().__init__(context, request)
        self.prompt_id = None
        self.file_id = None

    def publishTraverse(self, request, name):
        if self.prompt_id is None:
            self.prompt_id = name
        else:
            self.file_id = name
        return self

    def reply(self):
        if not self.prompt_id or not self.file_id:
            raise BadRequest("Missing prompt_id or file_id")

        meta = PromptFilesService._get_file_meta(self)  # reuse metadata lookup

        download = self.kyra.files.download(self.prompt_id, self.file_id)
        if isinstance(download, dict) and download.get("error"):
            raise BadRequest(download.get("error"))

        raw = b""
        if isinstance(download, dict):
            raw = download.get("content") or b""
        elif isinstance(download, bytes):
            raw = download

        self.request.response.setHeader(
            "Content-Type", meta.get("content_type") or "application/octet-stream"
        )
        self.request.response.setHeader(
            "Content-Disposition",
            f'attachment; filename="{meta.get("filename")}"',
        )
        self.request.response.setHeader("Content-Length", str(len(raw)))

        return raw
