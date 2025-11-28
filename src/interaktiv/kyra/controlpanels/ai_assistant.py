from interaktiv.kyra import _
from interaktiv.kyra.registry.ai_assistant import IAIAssistantSchema

from plone.app.registry.browser import controlpanel
from plone.z3cform import layout
from plone.app.registry.browser.controlpanel import ControlPanelFormWrapper
from zope.interface import Interface
from zope.component import adapter
from plone.restapi.controlpanels import RegistryConfigletPanel


class AIAssistantSettingsControlPanelForm(controlpanel.RegistryEditForm):
    schema = IAIAssistantSchema
    label = _('trans_label_controlpanel_ai_assistant_settings')


class AIAssistantSettingsControlPanel(controlpanel.ControlPanelFormWrapper):
    form = AIAssistantSettingsControlPanelForm


@adapter(Interface, Interface)
class AIAssistantSettingsConfigletPanel(RegistryConfigletPanel):
    schema = IAIAssistantSchema
    schema_prefix = "interaktiv.kyra.registry.ai_assistant.IAIAssistantSchema"
    configlet_id = "ai-assistant-settings"
    configlet_category_id = "Products"
    title = "AI Assistant Settings"
    group = "Products"


AIAssistantSettingsView = layout.wrap_form(AIAssistantSettingsControlPanelForm, ControlPanelFormWrapper)
