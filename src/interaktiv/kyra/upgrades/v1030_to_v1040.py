from plone import api


def upgrade(context):
    """Register AI Prompt Manager control panel entry."""
    setup = api.portal.get_tool("portal_setup")
    setup.runImportStepFromProfile(
        "profile-interaktiv.kyra:default", "controlpanel"
    )
