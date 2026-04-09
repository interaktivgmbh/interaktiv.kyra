from plone import api


def upgrade(context):
    """Replace OpenAI fields with edit backend URL fields."""
    setup = api.portal.get_tool("portal_setup")
    setup.runImportStepFromProfile(
        "profile-interaktiv.kyra:default", "plone.app.registry"
    )
