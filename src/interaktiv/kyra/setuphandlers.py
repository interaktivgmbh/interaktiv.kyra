from Products.CMFPlone.Portal import PloneSite
from plone.base.interfaces import INonInstallable
from zope.interface import implementer


@implementer(INonInstallable)
class HiddenProfiles(object):
    def getNonInstallableProfiles(self):
        return [
            'interaktiv.kyra:uninstall',
        ]


def post_install(context: PloneSite) -> None:
    pass


def uninstall(context: PloneSite) -> None:
    pass
