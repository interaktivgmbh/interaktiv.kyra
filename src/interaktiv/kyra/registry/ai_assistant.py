from interaktiv.kyra import _
from plone import schema
from zope.interface import Interface


class IAIAssistantSchema(Interface):
    gateway_url = schema.URI(
        title=_('trans_label_gateway_url'),
        description=_('trans_help_gateway_url'),
        required=True
    )

    keycloak_realms_url = schema.URI(
        title=_('trans_label_keycloak_realms_url'),
        description=_('trans_help_keycloak_realms_url'),
        required=True
    )

    keycloak_client_id = schema.TextLine(
        title=_('trans_label_keycloak_client_id'),
        description=_('trans_help_keycloak_client_id'),
        required=True
    )

    keycloak_client_secret = schema.Password(
        title=_('trans_label_keycloak_client_secret'),
        description=_('trans_help_keycloak_client_secret'),
        required=True
    )

    domain_id = schema.TextLine(
        title=_('trans_label_domain_id'),
        description=_('trans_help_domain_id'),
        default='plone',
        required=True
    )
