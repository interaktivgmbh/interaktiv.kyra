from zope.component import adapter
from zope.interface import Interface
from plone.restapi.controlpanels import RegistryConfigletPanel


@adapter(Interface, Interface)
class PromptManagerConfigletPanel(RegistryConfigletPanel):
    schema = None
    schema_prefix = "interaktiv.kyra"
    configlet_id = "ai-prompt-manager"
    configlet_category_id = "Products"
    title = "AI Prompt Manager"
    group = "Products"
