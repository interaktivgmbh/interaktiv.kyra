from plone.restapi.services import Service
from plone.restapi.deserializer import json_body
from plone import api
from zExceptions import BadRequest

REGISTRY_PREFIX = "interaktiv.kyra.registry.ai_assistant.IAIAssistantSchema"

FIELDS = (
    "gateway_url",
    "keycloak_realms_url",
    "keycloak_client_id",
    "keycloak_client_secret",
    "keycloak_token_expiration_time",
    "domain_id",
)


def _key(name: str) -> str:
    return f"{REGISTRY_PREFIX}.{name}"


def _get_registry():
    return api.portal.get_tool("portal_registry")


def _serialize(registry):
    """Immer die aktuell gespeicherten Werte aus portal_registry holen."""

    def get(name, default):
        record_name = _key(name)
        if record_name in registry.records:
            return registry[record_name]
        return default

    return {
        "gateway_url": get("gateway_url", "") or "",
        "keycloak_realms_url": get("keycloak_realms_url", "") or "",
        "keycloak_client_id": get("keycloak_client_id", "") or "",
        "keycloak_client_secret": get("keycloak_client_secret", "") or "",
        "keycloak_token_expiration_time": (
            get("keycloak_token_expiration_time", 1200) or 1200
        ),
        "domain_id": get("domain_id", "plone") or "plone",
    }


class AIAssistantSettingsGet(Service):
    """GET /++api++/@ai-assistant-settings"""

    def reply(self):
        registry = _get_registry()
        return _serialize(registry)


class AIAssistantSettingsPatch(Service):
    """PATCH /++api++/@ai-assistant-settings"""

    def reply(self):
        registry = _get_registry()
        data = json_body(self.request)

        if not isinstance(data, dict):
            raise BadRequest("JSON object expected")

        for name in FIELDS:
            if name not in data:
                continue

            value = data[name]

            if name == "keycloak_token_expiration_time":
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    raise BadRequest(
                        "keycloak_token_expiration_time must be an integer"
                    )

            registry[_key(name)] = value

        return _serialize(registry)
