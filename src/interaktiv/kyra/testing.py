from plone.app.testing import (
    FunctionalTesting,
    IntegrationTesting,
    PLONE_FIXTURE,
    PloneSandboxLayer,
)
from plone.testing.zope import WSGI_SERVER_FIXTURE


class InteraktivKyraLayer(PloneSandboxLayer):
    defaultBases = (PLONE_FIXTURE,)

    def setUpZope(self, app, configurationContext):
        # Load any other ZCML that is required for your tests.
        # The z3c.autoinclude feature is disabled in the Plone fixture base
        # layer.
        import plone.app.dexterity
        self.loadZCML(package=plone.app.dexterity)
        import plone.restapi
        self.loadZCML(package=plone.restapi)
        import interaktiv.kyra
        self.loadZCML(package=interaktiv.kyra)

    def setUpPloneSite(self, portal):
        self.applyProfile(portal, 'interaktiv.kyra:default')


INTERAKTIV_KYRA_FIXTURE = InteraktivKyraLayer()

INTERAKTIV_KYRA_INTEGRATION_TESTING = IntegrationTesting(
    bases=(INTERAKTIV_KYRA_FIXTURE,),
    name='InteraktivKyraLayer:IntegrationTesting',
)

INTERAKTIV_KYRA_FUNCTIONAL_TESTING = FunctionalTesting(
    bases=(INTERAKTIV_KYRA_FIXTURE, WSGI_SERVER_FIXTURE),
    name='InteraktivKyraLayer:FunctionalTesting',
)
