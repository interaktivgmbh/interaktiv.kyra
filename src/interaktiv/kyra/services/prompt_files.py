import uuid
import time

from plone import api
from plone.restapi.services import Service
from zope.interface import implementer
from zope.publisher.interfaces import IPublishTraverse
from zExceptions import BadRequest

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


@implementer(IPublishTraverse)
class PromptFilesService(Service):
    """Handles GET, POST, DELETE for /@ai-prompt-files/{prompt_id}[/{file_id}]"""

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
            return self.list_files()
        if method == "POST":
            return self.upload_files()
        if method == "DELETE":
            return self.delete_file()

        raise BadRequest("Unsupported HTTP method")

    def find_prompt(self):
        prompts = _get()
        for p in prompts:
            if p.get("id") == self.prompt_id:
                return p, prompts
        raise BadRequest(f"Prompt '{self.prompt_id}' not found")

    def list_files(self):
        prompt, _ = self.find_prompt()
        return {"files": prompt.get("files", [])}

    def upload_files(self):
        prompt, prompts = self.find_prompt()

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
            if not hasattr(f, "filename"):
                continue

            file_content = f.read()

            info = {
                "id": str(uuid.uuid4()),
                "filename": f.filename,
                "size": len(file_content or b""),
                "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            }

            uploaded_items.append(info)

        if not uploaded_items:
            raise BadRequest("No valid file data received")

        prompt.setdefault("files", []).extend(uploaded_items)
        _save(prompts)

        return {"uploaded": uploaded_items}

    def delete_file(self):
        if not self.file_id:
            raise BadRequest("Missing file ID")

        prompt, prompts = self.find_prompt()

        prompt["files"] = [
            f for f in prompt.get("files", []) if f.get("id") != self.file_id
        ]

        _save(prompts)

        return {"deleted": self.file_id}
