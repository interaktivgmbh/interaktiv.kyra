from plone.rest.service import Service
from interaktiv.kyra.api.files import Files


class FilesListGet(Service):
    def reply(self):
        api = Files(context=self.context, request=self.request)
        prompt_id = self.request.matchdict.get("prompt_id")
        return api.get(prompt_id)


class FilesPost(Service):
    def reply(self):
        api = Files(context=self.context, request=self.request)
        prompt_id = self.request.matchdict.get("prompt_id")
        upload = self.request.form.get("file")
        return api.upload(prompt_id, upload)


class FileItemDelete(Service):
    def reply(self):
        api = Files(context=self.context, request=self.request)
        prompt_id = self.request.matchdict.get("prompt_id")
        file_id = self.request.matchdict.get("file_id")
        return api.delete(prompt_id, file_id)
