from plone import api


def upgrade(context):
    """Register new DeepL registry fields by re-importing registry.xml."""
    setup = api.portal.get_tool("portal_setup")
    setup.runImportStepFromProfile("profile-interaktiv.kyra:default", "plone.app.registry")
