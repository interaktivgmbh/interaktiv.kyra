from interaktiv.kyra.api.chat import Chat
from interaktiv.kyra.api.prompts import Prompts


class KyraAPI:
    prompts: Prompts
    chat: Chat

    def __init__(self):
        self.prompts = Prompts()
        self.chat = Chat()
