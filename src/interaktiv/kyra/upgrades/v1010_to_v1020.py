from plone import api


def upgrade(context):
    """Apply rolemap updates for AI actions."""
    setup = api.portal.get_tool("portal_setup")
    setup.runImportStepFromProfile("profile-interaktiv.kyra:default", "rolemap")
