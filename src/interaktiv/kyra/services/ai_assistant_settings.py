# src/interaktiv/kyra/services/ai_assistant_settings.py

from plone.restapi.services import Service
from plone.registry.interfaces import IRegistry
from zope.component import getUtility
from zExceptions import BadRequest

from interaktiv.kyra.registry.ai_assistant import IAIAssistantSchema


def _get_settings():
    """Helper: Registry-Proxy für IAIAssistantSchema holen."""
    registry = getUtility(IRegistry)
    # nutzt prefix aus registry.xml automatisch (Interface-Dotted-Name)
    return registry.forInterface(IAIAssistantSchema)


class AIAssistantSettingsGet(Service):
    """GET /++api++/@ai-assistant-settings"""

    def reply(self):
        settings = _get_settings()

        return {
            "gateway_url": getattr(settings, "gateway_url", "") or "",
            "keycloak_realms_url": getattr(settings, "keycloak_realms_url", "") or "",
            "keycloak_client_id": getattr(settings, "keycloak_client_id", "") or "",
            "keycloak_client_secret": getattr(settings, "keycloak_client_secret", "") or "",
            "keycloak_token_expiration_time": getattr(
                settings, "keycloak_token_expiration_time", 1200
            )
            or 1200,
            "domain_id": getattr(settings, "domain_id", "plone") or "plone",
        }


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
        data = getattr(self.request, "json_body", None)
        if not isinstance(data, dict):
            raise BadRequest("JSON body expected")

        settings = _get_settings()

        for name in self.FIELDS:
            if name in data:
                value = data[name]
                # kleines Type-Safety für das Int-Feld
                if name == "keycloak_token_expiration_time":
                    try:
                        value = int(value)
                    except (TypeError, ValueError):
                        raise BadRequest(
                            "keycloak_token_expiration_time must be an integer"
                        )
                setattr(settings, name, value)

        # Antwort wie GET, damit das Frontend direkt frische Werte hat
        return {
            "gateway_url": settings.gateway_url,
            "keycloak_realms_url": settings.keycloak_realms_url,
            "keycloak_client_id": settings.keycloak_client_id,
            "keycloak_client_secret": settings.keycloak_client_secret,
            "keycloak_token_expiration_time": settings.keycloak_token_expiration_time,
            "domain_id": settings.domain_id,
        }
