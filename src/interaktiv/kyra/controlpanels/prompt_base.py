from Products.Five.browser import BrowserView
from Products.statusmessages.interfaces import IStatusMessage
from interaktiv.kyra.api import KyraAPI


class PromptManagerBaseView(BrowserView):
    kyra: KyraAPI
    prompt_id: str

    def __init__(self, context, request):
        super().__init__(context, request)
        self.kyra = KyraAPI()
        self.prompt_id = self._get_prompt_id()

    def _get_prompt_id(self) -> str:
        prompt_id = self.request.form.get('prompt_id')
        if not prompt_id:
            prompt_id = self.request.get('QUERY_STRING', '').replace('prompt_id=', '')
        return prompt_id

    def _add_message(self, message: str, msg_type: str) -> None:
        IStatusMessage(self.request).addStatusMessage(message, type=msg_type)
