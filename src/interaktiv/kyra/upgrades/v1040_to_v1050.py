from plone import api


_PREFIX = "interaktiv.kyra.registry.ai_assistant.IAIAssistantSchema"
_FEATURE_KEYS = [
    "feature_chat",
    "feature_translate",
    "feature_tag_mappings",
    "feature_glossary",
    "feature_prompts",
    "feature_assistant_run",
]


def upgrade(context):
    """Apply rolemap updates and clean up obsolete feature toggle records."""
    setup = api.portal.get_tool("portal_setup")
    setup.runImportStepFromProfile("profile-interaktiv.kyra:default", "rolemap")

    # Remove obsolete feature toggle records (now managed via permission matrix)
    registry = api.portal.get_tool("portal_registry")
    for key in _FEATURE_KEYS:
        full_key = f"{_PREFIX}.{key}"
        if full_key in registry.records:
            del registry.records[full_key]

    setup.runImportStepFromProfile(
        "profile-interaktiv.kyra:default", "plone.app.registry"
    )
