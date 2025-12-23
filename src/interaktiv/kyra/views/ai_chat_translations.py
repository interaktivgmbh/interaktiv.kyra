import json

from Products.Five import BrowserView
from interaktiv.kyra import project_name
from plone import api


class AIChatTranslationsView(BrowserView):
    """Simple view to expose current language + note for the AI chat widget."""

    NOTICE_MSGID = 'trans_ai_chat_language_note'

    def __call__(self):
        language = api.portal.get_current_language() or 'en'
        notice = self.context.translate(
            self.NOTICE_MSGID,
            domain=project_name,
            target_language=language,
        ) or self._default_notice(language)

        self.request.response.setHeader('Content-Type', 'application/json')
        return json.dumps(
            {
                'language': language,
                'notice': notice,
            }
        )

    def _default_notice(self, language: str) -> str:
        default = (
            'Kyra replies in the same language you type your prompt.',
            'Kyra reagiert in derselben Sprache, in der du deine Anfrage stellst.',
        )
        # fall back to english if we do not have a translation
        if language and language.startswith('de'):
            return default[1]
        return default[0]
