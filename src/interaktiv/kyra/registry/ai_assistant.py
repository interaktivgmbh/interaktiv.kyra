from interaktiv.kyra import _
from plone import schema
from zope.interface import Interface


# edit_backend_url and edit_backend_api_key are read via interface=IAIAssistantSchema
# but not declared here -- they rely on raw registry keys from upgrade steps and
# registry.xml. New installs get them from registry.xml but without schema validation,
# defaults, or control panel labels. Add them to this schema as the single source of truth.
class IAIAssistantSchema(Interface):
    gateway_url = schema.URI(
        title=_('trans_label_gateway_url'),
        description=_('trans_help_gateway_url'),
        required=True,
        default='http://localhost'
    )

    keycloak_realms_url = schema.URI(
        title=_('trans_label_keycloak_realms_url'),
        description=_('trans_help_keycloak_realms_url'),
        required=True,
        default='http://localhost'
    )

    keycloak_client_id = schema.TextLine(
        title=_('trans_label_keycloak_client_id'),
        description=_('trans_help_keycloak_client_id'),
        required=True,
        default=''
    )

    keycloak_client_secret = schema.Password(
        title=_('trans_label_keycloak_client_secret'),
        description=_('trans_help_keycloak_client_secret'),
        required=True,
        default=''
    )

    keycloak_token_expiration_time = schema.Int(
        title=_('trans_label_keycloak_token_expiration_time'),
        description=_('trans_help_keycloak_token_expiration_time'),
        required=True,
        default=0,
    )

    domain_id = schema.TextLine(
        title=_('trans_label_domain_id'),
        description=_('trans_help_domain_id'),
        default='plone',
        required=True,
    )

    deepl_api_key = schema.Password(
        title=_('DeepL API Key'),
        description=_('Authentication key for DeepL translation API'),
        default='',
        required=False,
    )


