from plone import api


def upgrade(context):
    """Register GitHub error reporting registry fields."""
    setup = api.portal.get_tool("portal_setup")
    setup.runImportStepFromProfile("profile-interaktiv.kyra:default", "plone.app.registry")
