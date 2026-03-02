from plone import api


def upgrade(context):
    """Add edit_backend_url registry field."""
    setup = api.portal.get_tool("portal_setup")
    setup.runImportStepFromProfile(
        "profile-interaktiv.kyra:default", "plone.app.registry"
    )
