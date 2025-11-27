# src/interaktiv/kyra/services/ai_assistant_settings.py

from plone.restapi.services import Service
from plone.restapi.deserializer import json_body
from plone.registry.interfaces import IRegistry
from zope.component import getUtility
from zExceptions import BadRequest

from interaktiv.kyra.registry.ai_assistant import IAIAssistantSchema


def _get_settings():
    """Registry-Proxy für IAIAssistantSchema holen."""
    registry = getUtility(IRegistry)
    return registry.forInterface(IAIAssistantSchema)


def _serialize(settings):
    """Antwort-JSON immer in derselben Struktur."""
    return {
        "gateway_url": getattr(settings, "gateway_url", "") or "",
        "keycloak_realms_url": getattr(settings, "keycloak_realms_url", "") or "",
        "keycloak_client_id": getattr(settings, "keycloak_client_id", "") or "",
        "keycloak_client_secret": getattr(settings, "keycloak_client_secret", "") or "",
        "keycloak_token_expiration_time": (
            getattr(settings, "keycloak_token_expiration_time", 1200) or 1200
        ),
        "domain_id": getattr(settings, "domain_id", "plone") or "plone",
    }


class AIAssistantSettingsGet(Service):
    """GET /++api++/@ai-assistant-settings"""

    def reply(self):
        settings = _get_settings()
        return _serialize(settings)


class AIAssistantSettingsPatch(Service):
    """PATCH /++api++/@ai-assistant-settings"""

    FIELDS = (
        "gateway_url",
        "keycloak_realms_url",
        "keycloak_client_id",
        "keycloak_client_secret",
        "keycloak_token_expiration_time",
        "domain_id",
    )

    def reply(self):
        # plone.restapi kümmert sich um das saubere Auslesen des JSON-Bodys
        data = json_body(self.request)

        if not isinstance(data, dict):
            raise BadRequest("JSON object expected")

        settings = _get_settings()

        for name in self.FIELDS:
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

            setattr(settings, name, value)

        # Aktuell persistierte Werte zurückgeben
        return _serialize(settings)
