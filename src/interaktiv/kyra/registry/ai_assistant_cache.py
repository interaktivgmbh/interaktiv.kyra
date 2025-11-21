from interaktiv.kyra import _

from plone import schema
from zope.interface import Interface


class IAIAssistantCacheSchema(Interface):
    keycloak_token_value = schema.Password(
        title=_('trans_label_keycloak_token_value'),
        description=_('trans_help_keycloak_token_value'),
        required=False
    )

    keycloak_token_timestamp = schema.Password(
        title=_('trans_label_keycloak_token_timestamp'),
        description=_('trans_help_keycloak_token_timestamp'),
        required=False
    )
