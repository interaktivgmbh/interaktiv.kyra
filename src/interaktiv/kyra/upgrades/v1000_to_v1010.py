from typing import Optional
import plone.api as api
from Products.GenericSetup.tool import SetupTool
from interaktiv.kyra.registry.ai_assistant import IAIAssistantSchema


# noinspection PyUnusedLocal
def upgrade(site_setup: Optional[SetupTool] = None) -> None:
    registry = api.portal.get_tool("portal_registry")
    interface_name = IAIAssistantSchema.__identifier__

    defaults = {
        "gateway_url": "http://localhost",
        "keycloak_realms_url": "http://localhost",
        "keycloak_client_id": "",
        "keycloak_client_secret": "",
        "keycloak_token_expiration_time": 0,
        "domain_id": "plone",
    }

    for field, value in defaults.items():
        key = f"{interface_name}.{field}"
        if key not in registry:
            registry[key] = value
