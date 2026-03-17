from plone import api


def upgrade(context):
    setup = api.portal.get_tool("portal_setup")
    setup.runImportStepFromProfile("profile-interaktiv.kyra:default", "rolemap")
