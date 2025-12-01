import uuid
import time
import base64

from plone import api
from plone.restapi.services import Service
from zExceptions import BadRequest

from zope.interface import implementer
from zope.publisher.interfaces import IPublishTraverse

ANNOTATION_KEY = "kyra.prompts"


def _get_annotations():
    portal = api.portal.get()
    ann = portal.__annotations__
    if ANNOTATION_KEY not in ann:
        ann[ANNOTATION_KEY] = []
    return ann


def _save(prompts):
    ann = _get_annotations()
    ann[ANNOTATION_KEY] = prompts
    return prompts


def _get():
    ann = _get_annotations()
    return ann.get(ANNOTATION_KEY, [])


def _find_prompt(prompt_id):
    prompts = _get()
    for p in prompts:
        if p.get("id") == prompt_id:
            return prompts, p
    raise BadRequest(f"Prompt '{prompt_id}' not found")


def _find_file(prompt, file_id):
    for f in prompt.get("files", []):
        if f.get("id") == file_id:
            return f
    return None


@implementer(IPublishTraverse)
class PromptFilesService(Service):
    """Handles GET, POST, DELETE for /@ai-prompt-files/{prompt_id}/{file_id}

    Varianten:
      GET  /@ai-prompt-files/{prompt_id}
           -> Liste der Dateien (nur Metadaten)

      GET  /@ai-prompt-files/{prompt_id}/{file_id}
           -> Metadaten + base64-kodierter Inhalt

      POST /@ai-prompt-files/{prompt_id}
           -> Datei(en) hochladen (Inhalt in Annotations, base64)

      DELETE /@ai-prompt-files/{prompt_id}/{file_id}
           -> Datei aus den Annotations entfernen
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
        _, prompt = _find_prompt(self.prompt_id)
        files = prompt.get("files", [])

        public_files = [
            {
                "id": f.get("id"),
                "filename": f.get("filename"),
                "size": f.get("size"),
                "created": f.get("created"),
                "content_type": f.get("content_type"),
            }
            for f in files
        ]

        return {"promptId": self.prompt_id, "files": public_files}

    def get_file(self):
        _, prompt = _find_prompt(self.prompt_id)
        f = _find_file(prompt, self.file_id)
        if not f:
            raise BadRequest(f"File '{self.file_id}' not found")

        return {
            "promptId": self.prompt_id,
            "id": f.get("id"),
            "filename": f.get("filename"),
            "size": f.get("size"),
            "created": f.get("created"),
            "content_type": f.get("content_type"),
            "data": f.get("data"),
        }

    def upload_files(self):
        prompts, prompt = _find_prompt(self.prompt_id)

        files = None

        if hasattr(self.request, "form"):
            files = self.request.form.get("file")

        if files is None and "file" in self.request:
            files = self.request["file"]

        if files is None:
            raise BadRequest("No file provided")

        if not isinstance(files, list):
            files = [files]

        uploaded_items = []

        for f in files:
            raw = f.read()
            b64 = base64.b64encode(raw).decode("ascii")

            headers = getattr(f, "headers", None) or {}
            content_type = (
                headers.get("content-type")
                or getattr(f, "content_type", None)
                or "application/octet-stream"
            )

            info = {
                "id": str(uuid.uuid4()),
                "filename": getattr(f, "filename", ""),
                "content_type": content_type,
                "size": len(raw),
                "created": time.strftime("%Y-%m-%d %H:%M:%S"),
                "data": b64,
            }

            uploaded_items.append(info)

        if "files" not in prompt:
            prompt["files"] = []

        prompt["files"].extend(uploaded_items)
        _save(prompts)

        public_items = [
            {
                "id": f["id"],
                "filename": f["filename"],
                "size": f["size"],
                "created": f["created"],
                "content_type": f["content_type"],
            }
            for f in uploaded_items
        ]

        return {
            "promptId": self.prompt_id,
            "uploaded": public_items,
        }

    def delete_file(self):
        if not self.file_id:
            raise BadRequest("Missing file ID")

        prompts, prompt = _find_prompt(self.prompt_id)

        prompt["files"] = [
            f for f in prompt.get("files", []) if f.get("id") != self.file_id
        ]

        _save(prompts)

        return {
            "promptId": self.prompt_id,
            "deleted": self.file_id,
        }
