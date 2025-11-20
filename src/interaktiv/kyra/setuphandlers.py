from Products.CMFPlone.Portal import PloneSite
from plone.base.interfaces import ITinyMCESchema, INonInstallable
from plone.registry.interfaces import IRegistry
from zope.component import getUtility
from zope.interface import implementer

CUSTOM_PLUGINS = [
    {
        'name': 'ai-assistant',
        'path': '/++theme++interaktiv.kyra.components/js/ai-assistant-plugin.js',
        'visible': True
    }
]


@implementer(INonInstallable)
class HiddenProfiles(object):
    # noinspection PyPep8Naming,PyMethodMayBeStatic
    def getNonInstallableProfiles(self):
        """Hide uninstall profile from site-creation and quickinstaller."""
        return [
            'interaktiv.kyra:uninstall',
        ]


def add_tinymce_plugins() -> None:
    registry = getUtility(IRegistry)
    settings = registry.forInterface(
        ITinyMCESchema, prefix='plone'
    )
    for plugin in CUSTOM_PLUGINS:
        plugin_entry = plugin['name'] + '|' + plugin['path']

        custom_plugins = settings.custom_plugins
        if plugin_entry not in custom_plugins:
            custom_plugins.append(plugin_entry)
            settings.custom_plugins = custom_plugins

        if plugin['visible']:
            toolbar = settings.toolbar
            if plugin['name'] not in toolbar:
                settings.toolbar = toolbar + ' ' + plugin['name']


def remove_tinymce_plugins() -> None:
    registry = getUtility(IRegistry)
    settings = registry.forInterface(
        ITinyMCESchema, prefix='plone'
    )
    for plugin in CUSTOM_PLUGINS:
        plugin_entry = plugin['name'] + '|' + plugin['path']

        custom_plugins = settings.custom_plugins
        if plugin_entry in custom_plugins:
            custom_plugins.remove(plugin_entry)
            settings.custom_plugins = custom_plugins

        toolbar = settings.toolbar
        plugin_toolbar_entry = ' ' + plugin['name']
        if plugin_toolbar_entry in toolbar:
            settings.toolbar = toolbar.replace(plugin_toolbar_entry, '')


# noinspection PyUnusedLocal
def post_install(context: PloneSite) -> None:
    """Post install script"""
    # Add our custom TinyMce plugins to Plone Configuration
    add_tinymce_plugins()


# noinspection PyUnusedLocal
def uninstall(context: PloneSite) -> None:
    """Uninstall script"""
    # Remove our custom TinyMce plugins from Plone Configuration
    remove_tinymce_plugins()
