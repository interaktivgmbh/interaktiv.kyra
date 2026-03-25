from interaktiv.kyra.api.chat import Chat
from interaktiv.kyra.api.prompts import Prompts


# Files are stored in Plone annotations via the @ai-prompt-files REST service,
# not through a gateway API client like the reference project does.
class KyraAPI:
    prompts: Prompts
    chat: Chat

    def __init__(self):
        self.prompts = Prompts()
        self.chat = Chat()
