from interaktiv.kyra import _
from interaktiv.kyra.registry.ai_assistant import IAIAssistantSchema

from plone.app.registry.browser import controlpanel


class AIAssistantSettingsControlPanelForm(controlpanel.RegistryEditForm):
    schema = IAIAssistantSchema
    label = _("AI Assistant Settings")


class AIAssistantSettingsControlPanel(controlpanel.ControlPanelFormWrapper):
    form = AIAssistantSettingsControlPanelForm
