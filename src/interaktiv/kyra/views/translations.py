"""Translation service view for AI Assistant TinyMCE Plugin.

Provides a JSON endpoint for fetching translated strings in the
current user's language.
"""

import json

from Products.Five import BrowserView
from interaktiv.kyra import project_name
from plone import api


class TranslationsView(BrowserView):
    msgids = [
        'trans_ai_assistant_menu_title',
        'trans_ai_assistant_menu_tooltip',
        'trans_ai_assistant_no_instructions',
        'trans_ai_assistant_no_instructions_message',
        'trans_ai_assistant_loading_error',
        'trans_ai_assistant_loading_error_message',
        'trans_ai_assistant_fetch_error',
        'trans_ai_assistant_processing',
        'trans_ai_assistant_apply_error',
        'trans_ai_assistant_select_text_warning',
        'trans_ai_assistant_select_text_for_instruction',
        'trans_ai_assistant_success',
        'trans_ai_assistant_manual_prompt_unavailable',
        'trans_ai_assistant_category_uncategorized',
    ]

    def __call__(self):
        language = api.portal.get_current_language()
        translations = {msgid: self._translate(msgid, language) for msgid in self.msgids}

        self.request.response.setHeader('Content-Type', 'application/json')
        return json.dumps({
            'language': language,
            'translations': translations
        })

    def _translate(self, msgid, language):
        return self.context.translate(msgid, domain=project_name, target_language=language)
