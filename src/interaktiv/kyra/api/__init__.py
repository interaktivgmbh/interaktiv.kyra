from interaktiv.kyra.api.files import Files
from interaktiv.kyra.api.chat import Chat
from interaktiv.kyra.api.prompts import Prompts


class KyraAPI:
    prompts: Prompts
    files: Files
    chat: Chat

    def __init__(self):
        self.prompts = Prompts()
        self.files = Files()
        self.chat = Chat()
