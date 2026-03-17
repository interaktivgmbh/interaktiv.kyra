import base64
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from interaktiv.kyra import logger
from plone import api
from plone.protect.interfaces import IDisableCSRFProtection
from plone.restapi.deserializer import json_body
from plone.restapi.services import Service
from zExceptions import BadRequest, NotFound
from zope.annotation.interfaces import IAnnotations
from zope.interface import alsoProvides, implementer
from zope.publisher.interfaces import IPublishTraverse

FILES_STORAGE_KEY = "interaktiv.kyra.prompt_files"


def _get_files_store() -> Dict[str, List[Dict[str, Any]]]:
    portal = api.portal.get()
    annotations = IAnnotations(portal)
    store = annotations.get(FILES_STORAGE_KEY)
    if not isinstance(store, dict):
        store = {}
        annotations[FILES_STORAGE_KEY] = store
    return store


def _persist_store(store: Dict[str, List[Dict[str, Any]]]) -> None:
    portal = api.portal.get()
    annotations = IAnnotations(portal)
    annotations[FILES_STORAGE_KEY] = store


def list_files(prompt_id: str) -> List[Dict[str, Any]]:
    store = _get_files_store()
    files = store.get(prompt_id, [])
    return [
        {k: v for k, v in f.items() if k != "data"}
        for f in files
    ]


def get_file(prompt_id: str, file_id: str) -> Optional[Dict[str, Any]]:
    store = _get_files_store()
    for f in store.get(prompt_id, []):
        if f.get("id") == file_id:
            return f
    return None


def add_file(prompt_id: str, filename: str, content_type: str,
             data: bytes) -> Dict[str, Any]:
    file_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()

    file_entry = {
        "id": file_id,
        "prompt_id": prompt_id,
        "filename": filename,
        "content_type": content_type,
        "size": len(data),
        "data": base64.b64encode(data).decode("ascii"),
        "created": now,
    }

    store = _get_files_store()
    if prompt_id not in store:
        store[prompt_id] = []
    store[prompt_id].append(file_entry)
    _persist_store(store)

    logger.info(
        "[KYRA FILES] uploaded file %s (%s) for prompt %s",
        filename, file_id, prompt_id,
    )
    return {k: v for k, v in file_entry.items() if k != "data"}


def delete_file(prompt_id: str, file_id: str) -> None:
    store = _get_files_store()
    files = store.get(prompt_id, [])
    new_files = [f for f in files if f.get("id") != file_id]
    if len(new_files) == len(files):
        raise BadRequest(f"File '{file_id}' not found for prompt '{prompt_id}'")
    store[prompt_id] = new_files
    _persist_store(store)
    logger.info("[KYRA FILES] deleted file %s from prompt %s", file_id, prompt_id)


def delete_files_for_prompt(prompt_id: str) -> None:
    store = _get_files_store()
    if prompt_id in store:
        del store[prompt_id]
        _persist_store(store)


@implementer(IPublishTraverse)
class PromptFilesService(Service):

    def __init__(self, context, request):
        super().__init__(context, request)
        alsoProvides(self.request, IDisableCSRFProtection)
        self._path_segments = []

    def publishTraverse(self, request, name):
        self._path_segments.append(name)
        return self

    @property
    def prompt_id(self) -> Optional[str]:
        return self._path_segments[0] if len(self._path_segments) >= 1 else None

    @property
    def file_id(self) -> Optional[str]:
        return self._path_segments[1] if len(self._path_segments) >= 2 else None

    def reply(self):
        method = self.request.method.upper()

        if not self.prompt_id:
            raise BadRequest("Missing prompt_id in URL")

        if method == "GET":
            return self._handle_get()
        if method == "POST":
            return self._handle_post()
        if method == "DELETE":
            return self._handle_delete()

        raise BadRequest("Unsupported method")

    def _handle_get(self):
        if self.file_id:
            f = get_file(self.prompt_id, self.file_id)
            if f is None:
                raise NotFound(f"File '{self.file_id}' not found")
            return f
        return {"files": list_files(self.prompt_id)}

    def _handle_post(self):
        file_field = self.request.form.get("file")
        if file_field is None:
            raise BadRequest("No 'file' field in upload")

        if hasattr(file_field, "read"):
            data = file_field.read()
            filename = getattr(file_field, "filename", "upload")
            content_type = getattr(
                file_field, "content_type",
                getattr(file_field.headers, "get", lambda *a: "application/octet-stream")("content-type", "application/octet-stream")
                if hasattr(file_field, "headers") else "application/octet-stream"
            )
        else:
            raise BadRequest("Invalid file upload")

        meta = add_file(self.prompt_id, filename, content_type, data)
        return {"result": "ok", "file": meta}

    def _handle_delete(self):
        if not self.file_id:
            raise BadRequest("Missing file_id in URL")
        delete_file(self.prompt_id, self.file_id)
        return {"result": "ok", "id": self.file_id}
