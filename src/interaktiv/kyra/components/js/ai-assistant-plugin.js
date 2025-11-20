/**
 * TinyMCE AI-Assistant Plugin
 * Dynamic plugin for AI-Assistant menu with API integration
 */

(function() {
  'use strict';

  tinymce.PluginManager.add('ai-assistant', function(editor) {

    let translations = {};
    let currentLanguage = 'de';

    const t = (key, replacements = {}) => {
      let text = translations[key] || key;
      Object.keys(replacements).forEach(placeholder => {
        text = text.replace(`{${placeholder}}`, replacements[placeholder]);
      });
      return text;
    };

    const loadTranslations = async () => {
      try {
        const response = await fetch(`${getApiBaseUrl()}/@@ai-assistant-translations`, {
          method: 'GET',
          headers: { 'Accept': 'application/json' }
        });
        if (response.ok) {
          const data = await response.json();
          translations = data.translations || {};
          currentLanguage = data.language || 'en';
        }
      } catch (error) {
        console.error('Failed to load translations:', error);
        translations = {
          'trans_ai_assistant_no_prompts': 'No prompts available',
          'trans_ai_assistant_no_prompts_message': 'No AI prompts found. Create prompts via the API.',
          'trans_ai_assistant_loading_error': 'Loading error',
          'trans_ai_assistant_loading_error_message': 'Error loading AI prompts. Check the API connection.',
          'trans_ai_assistant_fetch_error': 'Error loading AI prompts: {error}',
          'trans_ai_assistant_processing': '⏳ AI prompt is being processed... This may take a moment.',
          'trans_ai_assistant_apply_error': 'Error applying prompt: {error}',
          'trans_ai_assistant_select_text_warning': 'Please select text first.',
          'trans_ai_assistant_select_text_for_prompt': 'Please select text first to apply the prompt.',
          'trans_ai_assistant_success': '"{name}" applied successfully.',
          'trans_ai_assistant_manual_prompt_unavailable': 'AI Manual Prompt Extension is not available.',
          'trans_ai_assistant_category_uncategorized': 'Uncategorized',
        };
      }
    };

    // API Configuration helpers
    const getApiBaseUrl = () => {
      return '.';
    };
    const getHeaders = () => ({
      'Content-Type': 'application/json',
      'Accept': 'application/json',
    });

    // API Service Layer
    const apiService = {
      async fetchPrompts() {
        try {
          const response = await fetch(`${getApiBaseUrl()}/prompts?page=1&size=100`, { method: 'GET', headers: getHeaders() });
          if (!response.ok) throw new Error(`HTTP ${response.status}: ${response.statusText}`);
          const data = await response.json();
          return data.prompts || [];
        } catch (error) {
          console.error('Failed to fetch prompts:', error);
          editor.notificationManager.open({
            text: t('trans_ai_assistant_fetch_error', { error: error.message }),
            type: 'error',
            timeout: 5000
          });
          return [];
        }
      },

      async applyPrompt(promptId, selectedText) {
        let notification = null;
        try {
          notification = editor.notificationManager.open({
            text: t('trans_ai_assistant_processing'),
            type: 'info',
            timeout: false
          });

          const requestBody = {
            query: 'Apply prompt to selected text',
            text: selectedText,
            include_context: true
          };

          const response = await fetch(`${getApiBaseUrl()}/prompts/${promptId}/apply`, {
            method: 'POST', headers: getHeaders(), body: JSON.stringify(requestBody)
          });

          if (!response.ok) {
            const errorData = await response.json();
            let errorMessage = `HTTP ${response.status}: ${response.statusText}`;
            if (errorData.detail) {
              if (typeof errorData.detail === 'string') errorMessage = errorData.detail;
              else if (Array.isArray(errorData.detail)) errorMessage = errorData.detail.map(err => err.msg || err.message || JSON.stringify(err)).join(', ');
              else if (typeof errorData.detail === 'object') errorMessage = JSON.stringify(errorData.detail);
            }
            throw new Error(errorMessage);
          }

          const data = await response.json();
          if (notification) notification.close();
          return data.result || data.response || '';
        } catch (error) {
          if (notification) notification.close();
          console.error('Failed to apply prompt:', error);
          editor.notificationManager.open({
            text: t('trans_ai_assistant_apply_error', { error: error.message }),
            type: 'error',
            timeout: 5000
          });
          return null;
        }
      }
    };

    // Group prompts by category
    function groupPromptsByCategory(prompts) {
      const categorized = {};
      const uncategorized = [];
      prompts.forEach(prompt => {
        if (!prompt.prompt) return;
        const categories = prompt.metadata?.categories || [];
        if (categories.length === 0) uncategorized.push(prompt);
        else categories.forEach(category => {
          if (!categorized[category]) categorized[category] = [];
          categorized[category].push(prompt);
        });
      });
      const result = {};
      Object.keys(categorized).sort().forEach(category => {
        result[category] = categorized[category].sort((a,b) => a.name.localeCompare(b.name));
      });
      if (uncategorized.length > 0) {
        result[t('trans_ai_assistant_category_uncategorized')] = uncategorized.sort((a,b) => a.name.localeCompare(b.name))
      }
      return result;
    }

    // Generate menu items from grouped prompts
    function generateMenuItems(groupedPrompts) {
      const menuItems = [];

      const categories = Object.keys(groupedPrompts);
      categories.forEach((category, index) => {
        const prompts = groupedPrompts[category];

        // Add separator before uncategorized if it exists and isn't the first item
        const uncategorizedLabel = t('trans_ai_assistant_category_uncategorized');
        if (category === uncategorizedLabel && index > 0) {
          menuItems.push({ type: 'separator' });
        }

        menuItems.push({
          type: 'nestedmenuitem',
          text: category,
          getSubmenuItems: function() {
            return prompts.map(prompt => {
              return {
                type: 'menuitem',
                text: prompt.name,
                onAction: () => {
                  handlePromptClick(prompt);
                }
              };
            });
          }
        });
      });

      return menuItems;
    }

    let cachedPrompts = [];
    let menuItems = [];

    // Handle prompt click
    async function handlePromptClick(prompt) {
      editor.dispatch('closeAllMenus');

      if (editor.ui && editor.ui.registry) {
        const toolbars = document.querySelectorAll('.tox-toolbar__overflow, .tox-menu, .tox-collection');
        toolbars.forEach(toolbar => { if(toolbar.style) toolbar.style.display = 'none'; });
        const buttons = document.querySelectorAll('.tox-tbtn--enabled, .tox-tbtn--active, [aria-pressed="true"]');
        buttons.forEach(button => {
          button.classList.remove('tox-tbtn--enabled', 'tox-tbtn--active');
          button.setAttribute('aria-pressed', 'false');
          button.blur();
        });
      }

      document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', keyCode:27, bubbles:true, cancelable:true}));
      editor.focus();

      setTimeout(async () => {
        const selectedText = editor.selection.getContent({format: 'text'});
        if (!selectedText || selectedText.trim() === '') {
          editor.notificationManager.open({
            text: t('trans_ai_assistant_select_text_for_prompt'),
            type: 'warning',
            timeout: 3000
          });
          return;
        }

        const bookmark = editor.selection.getBookmark();
        const originalContent = editor.selection.getContent();

        const loadingHtml = `
          <span class="mce-ai-processing" style="
            display: inline-block;
            position: relative;
            background-color: #fff3cd;
            border: 2px solid #ff6900;
            border-radius: 4px;
            padding: 4px 8px;
            margin: 0 2px;
            animation: pulse 1.5s ease-in-out infinite;
          ">
            <span style="opacity: 0.6;">${selectedText}</span>
            <span class="mce-ai-spinner" style="
              display: inline-block;
              margin-left: 8px;
              width: 14px;
              height: 14px;
              border: 2px solid #ff6900;
              border-right-color: transparent;
              border-radius: 50%;
              animation: spin 0.8s linear infinite;
              vertical-align: middle;
            "></span>
          </span>
        `;
        editor.selection.setContent(loadingHtml);

        const action = prompt.metadata?.action || 'replace';
        const result = await apiService.applyPrompt(prompt.id, selectedText);

        if (result) {
          editor.undoManager.transact(() => {
            editor.selection.moveToBookmark(bookmark);
            const trimmedResult = result.trim();
            if (action.toLowerCase() === 'append') {
              editor.selection.setContent(selectedText + ' ' + trimmedResult);
            } else {
              editor.selection.setContent(trimmedResult);
            }
          });

          editor.notificationManager.open({
            text: t('trans_ai_assistant_success', { name: prompt.name }),
            type: 'success',
            timeout: 3000
          });
        } else {
          editor.selection.moveToBookmark(bookmark);
          editor.selection.setContent(originalContent);
        }
      }, 50);
    }

    // Initialize plugin by fetching and registering prompts
    async function initializePlugin() {
      try {
        await loadTranslations();

        cachedPrompts = await apiService.fetchPrompts();
        const groupedPrompts = groupPromptsByCategory(cachedPrompts);
        menuItems = generateMenuItems(groupedPrompts);
        registerPromptButtons();

        if(menuItems.length === 0) {
          menuItems = [{
            type: 'menuitem',
            text: t('trans_ai_assistant_no_prompts'),
            onAction: () => editor.notificationManager.open({
              text: t('trans_ai_assistant_no_prompts_message'),
              type: 'warning',
              timeout: 3000
            })
          }];
        }
      } catch(error) {
        console.error('Plugin initialization failed:', error);
        menuItems = [{
          type: 'menuitem',
          text: t('trans_ai_assistant_loading_error'),
          onAction: () => editor.notificationManager.open({
            text: t('trans_ai_assistant_loading_error_message'),
            type: 'error',
            timeout: 3000
          })
        }];
      }
    }

    // Register individual prompt buttons for bubble menu
    function registerPromptButtons() {
      cachedPrompts.forEach(prompt => {
        if(!prompt.prompt) return;
        const buttonName = `ai-prompt-${prompt.id}`;
        editor.ui.registry.addButton(buttonName, {
          text: prompt.name.length > 15 ? prompt.name.slice(0,15) + '...' : prompt.name,
          tooltip: prompt.name,
          onAction: () => handlePromptClick(prompt)
        });
      });
    }

    // Register UI buttons & menus
    editor.ui.registry.addMenuButton('ai-assistant', {
      text: 'AI',
      tooltip: 'AI Assistant Functions',
      fetch: (callback) => callback(menuItems)
    });

    // Add styles & initialize plugin on editor init
    editor.on('init', () => {
      const style = document.createElement('style');
      style.textContent = `
        @keyframes pulse { 0%{opacity:1;transform:scale(1);}50%{opacity:0.8;transform:scale(0.98);}100%{opacity:1;transform:scale(1);} }
        @keyframes spin { from{transform:rotate(0deg);} to{transform:rotate(360deg);} }
        .ai-spinner { animation: spin 1s linear infinite; }
        .mce-ai-processing { animation: pulse 1.5s ease-in-out infinite; box-shadow: 0 2px 8px rgba(255, 105, 0, 0.3); }
      `;
      document.head.appendChild(style);

      initializePlugin();
    });

    return {
      getMetadata: () => ({
        name: 'AI-Assistant Plugin',
        url: 'https://example.com'
      })
    };

  });
})();
