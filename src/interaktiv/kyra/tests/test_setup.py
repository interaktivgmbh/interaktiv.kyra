import unittest

from interaktiv.kyra.interfaces import IInteraktivKyraLayer
from interaktiv.kyra.testing import INTERAKTIV_KYRA_INTEGRATION_TESTING
from plone.app.testing import TEST_USER_ID, setRoles
from plone.base.interfaces import ITinyMCESchema
from plone.base.utils import get_installer
from plone.browserlayer import utils
from plone.registry.interfaces import IRegistry
from zope.component import getUtility


class TestSetup(unittest.TestCase):
    layer = INTERAKTIV_KYRA_INTEGRATION_TESTING
    product_name = 'interaktiv.kyra'

    def setUp(self):
        self.app = self.layer['app']
        self.portal = self.layer['portal']
        self.request = self.layer['request']
        setRoles(self.portal, TEST_USER_ID, ['Manager', 'Site Administrator'])

    def test_product_installed(self):
        installer = get_installer(self.portal, self.request)
        self.assertTrue(installer.is_product_installed(self.product_name))

    def test_browserlayer(self):
        self.assertIn(IInteraktivKyraLayer, utils.registered_layers())

    def test_setup_updates_tiny_mce_settings_custom_plugins(self):
        registry = getUtility(IRegistry)
        settings = registry.forInterface(
            ITinyMCESchema, prefix='plone'
        )

        custom_plugins = settings.custom_plugins
        expected_custom_plugins = [
            "ai-assistant|/++theme++interaktiv.kyra.components/js/ai-assistant-plugin.js"
        ]
        self.assertListEqual(custom_plugins, expected_custom_plugins)

    def test_setup_updates_tiny_mce_toolbar(self):
        registry = getUtility(IRegistry)
        settings = registry.forInterface(
            ITinyMCESchema, prefix='plone'
        )

        self.assertIn('ai-assistant', settings.toolbar)


class TestUninstall(unittest.TestCase):
    layer = INTERAKTIV_KYRA_INTEGRATION_TESTING
    product_name = 'interaktiv.kyra'

    def setUp(self):
        self.app = self.layer['app']
        self.portal = self.layer['portal']
        self.request = self.layer['request']
        setRoles(self.portal, TEST_USER_ID, ['Manager', 'Site Administrator'])

        installer = get_installer(self.portal, self.request)
        installer.uninstall_product(self.product_name)

    def test_product_uninstalled(self):
        installer = get_installer(self.portal, self.request)
        self.assertFalse(installer.is_product_installed(self.product_name))

    def test_browserlayer_removed(self):
        self.assertNotIn(IInteraktivKyraLayer, utils.registered_layers())

    def test_setup_updates_tiny_mce_settings_custom_plugins(self):
        registry = getUtility(IRegistry)
        settings = registry.forInterface(
            ITinyMCESchema, prefix='plone'
        )

        custom_plugins = settings.custom_plugins
        expected_custom_plugins = []
        self.assertListEqual(custom_plugins, expected_custom_plugins)