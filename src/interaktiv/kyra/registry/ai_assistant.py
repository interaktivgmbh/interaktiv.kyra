from interaktiv.kyra import _
from plone import schema
from zope.interface import Interface


class IAIAssistantSchema(Interface):
    gateway_url = schema.TextLine(
        title=_('trans_label_gateway_url'),
        description=_('trans_help_gateway_url'),
        required=True,
        default='http://localhost'
    )

    keycloak_realms_url = schema.TextLine(
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

    openai_api_key = schema.TextLine(
        title=u"OpenAI API Key",
        description=u"API key for the LLM powering the Layout Agent (OpenAI-compatible).",
        required=False,
        default=u"",
    )

    openai_model = schema.TextLine(
        title=u"Layout Agent LLM Model",
        description=u"Model ID for the Layout Agent (e.g. gpt-5.4-mini, gpt-4o).",
        required=False,
        default=u"gpt-5.4-mini",
    )

    github_token = schema.Password(
        title=_('GitHub Token'),
        description=_('Personal access token for auto-creating issues from errors. Scope: repo.'),
        default='',
        required=False,
    )

    github_repo = schema.TextLine(
        title=_('GitHub Repository'),
        description=_('Repository for error issues (e.g. interaktivgmbh/volto-interaktiv-kyra).'),
        default='interaktivgmbh/volto-interaktiv-kyra',
        required=False,
    )

