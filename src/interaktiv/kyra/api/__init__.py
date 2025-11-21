from interaktiv.kyra.api.files import Files
from interaktiv.kyra.api.prompts import Prompts


class KyraAPI:
    """Main API client for Kyra AI assistant service.

    This class serves as the central entry point for all Kyra API operations.

    Attributes:
        prompts: Interface for prompt-related operations (CRUD, apply).
        files: Interface for file-related operations (upload, download, delete).
    """

    prompts: Prompts
    files: Files

    def __init__(self):
        self.prompts = Prompts()
        self.files = Files()
