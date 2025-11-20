from interaktiv.kyra.api.files import Files
from interaktiv.kyra.api.prompts import Prompts


class KyraAPI:
    prompts: Prompts
    files: Files

    def __init__(self):
        self.prompts = Prompts()
        self.files = Files()
