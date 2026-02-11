from Products.CMFPlone.Portal import PloneSite
from plone.base.interfaces import INonInstallable
from zope.interface import implementer


@implementer(INonInstallable)
class HiddenProfiles(object):
    # noinspection PyPep8Naming,PyMethodMayBeStatic
    def getNonInstallableProfiles(self):
        """Hide uninstall profile from site-creation and quickinstaller."""
        return [
            'interaktiv.kyra:uninstall',
        ]


# noinspection PyUnusedLocal
def post_install(context: PloneSite) -> None:
    """Post install script"""
    pass


# noinspection PyUnusedLocal
def uninstall(context: PloneSite) -> None:
    """Uninstall script"""
    pass
