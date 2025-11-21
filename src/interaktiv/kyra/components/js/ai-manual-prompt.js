/**
 * TinyMCE AI Manual Prompt Extension
 * Handles manual prompt dialog, bubble menu, and related functionality
 */

(function() {
  'use strict';

  // Check if TinyMCE is available
  if (typeof tinymce === 'undefined') {
    console.error('TinyMCE is required for AI Manual Prompt Extension');
    return;
  }

  // Simple icon helper using emoji/unicode characters
  const getIcon = (iconName) => {
    switch (iconName) {
      case 'close': return '✕';
      case 'reload': return '↻';
      case 'plus': return '+';
      case 'rotate-right': return '⟳';
      case 'send': return '➤';
      default: return `[${iconName}]`;
    }
  };

  // API Configuration helpers
  const getApiBaseUrl = () => {
    if (window.TINYMCE_CONFIG && window.TINYMCE_CONFIG.apiBaseUrl) return window.TINYMCE_CONFIG.apiBaseUrl;
      return '.';
  };

  const getDomainId = (editor) => {
    const domainId = editor.getParam('KyraDomainId');
    if (domainId) {
      return domainId
    }
    return 'example.com';
  };

  const getHeaders = (editor) => ({
    'x-kyra-domain-id': getDomainId(editor),
    'Content-Type': 'application/json',
    'Accept': 'application/json',
  });

  // Simple prompt validation - First line of defense
  function validatePrompt(prompt) {
    const text = prompt.toLowerCase().trim();

    // Check minimum length
    if (text.length < 3) {
      return { valid: false, message: 'Anweisung zu kurz. Bitte geben Sie eine aussagekräftige Anweisung ein.' };
    }

    // Obvious non-editing prompts (blacklist approach)
    const blockedPatterns = [
      /^(hi|hello|hey|hallo)$/i,
      /^(what|was|wer|wie|wo|wann)\s+(is|ist|sind|bist)/i,
      /^\d+\s*[+\-*/]\s*\d+/,  // Math operations
      /(weather|wetter|temperature|temperatur)/i,
      /(personal|persönlich|private|privat)/i,
      /^(tell me|erzähl mir|sag mir)\s+(about|über)/i,
      // Question/analysis patterns that aren't editing
      /^(how many|wie viele|count|zähle|how much|wie viel)/i,
      /^(what does|was bedeutet|what means|was heißt)/i,
      /^(explain|erkläre|describe|beschreibe)\s+(what|was|this|dies)/i,
      /^(find|finde|search|suche|locate)/i,
      /^(calculate|berechne|compute|rechne)/i,
      /^(analyze|analysiere|analysis|analyse)/i
    ];

    const isBlocked = blockedPatterns.some(pattern => pattern.test(text));
    if (isBlocked) {
      return {
        valid: false,
        message: 'Bitte geben Sie eine textbezogene Anweisung ein (z.B. "übersetze", "korrigiere", "verbessere").'
      };
    }

    // If it contains any editing keywords, definitely allow it
    const editingKeywords = [
      'übersetze', 'translate', 'korrigiere', 'correct', 'verbessere', 'improve',
      'schreibe', 'write', 'umschreibe', 'rewrite', 'zusammenfasse', 'summarize',
      'kürze', 'shorten', 'erweitere', 'expand', 'formatiere', 'format',
      'erkläre', 'explain', 'paraphrasiere', 'paraphrase', 'ändere', 'change',
      'text', 'satz', 'sentence', 'paragraph', 'absatz'
    ];

    const hasEditingKeyword = editingKeywords.some(keyword => text.includes(keyword));

    // If has editing keywords, definitely valid
    if (hasEditingKeyword) {
      return { valid: true, confident: true };
    }

    // Otherwise, we're not sure - let LLM decide
    return { valid: true, confident: false };
  }

  // Manual Prompt API Service
  const manualPromptApiService = {
    async sendCustomPrompt(prompt, selectedText, needsLLMValidation = false, editor) {
      try {
        let finalPrompt = prompt;

        // Only add validation wrapper if client-side wasn't confident
        if (needsLLMValidation) {
          finalPrompt = `
VALIDATION TASK: You must first determine if this request is appropriate for a text editing assistant.

REJECT if the request is:
- Personal questions (e.g., "how are you?", "what's your name?")
- General knowledge queries (e.g., "what is the capital of France?")
- Math calculations (e.g., "what is 2+2?")
- Current events or news (e.g., "what happened today?")
- Technical support unrelated to text (e.g., "how to install software?")
- Analysis requests without editing intent (e.g., "how many words are here?", "what does this mean?")
- Weather, time, or location queries
- Any request that doesn't involve modifying, improving, or working with the provided text

ACCEPT if the request involves:
- Text editing (correct, improve, rewrite, shorten, expand)
- Translation or language tasks
- Text formatting or style changes
- Content creation or enhancement
- Grammar, spelling, or clarity improvements
- Text analysis WITH editing purpose (e.g., "analyze and improve this text")

User request: "${prompt}"

INSTRUCTIONS:
1. If REJECT: Respond ONLY with: "⚠️ Diese Anfrage ist nicht textbezogen. Bitte geben Sie eine Anweisung zur Textbearbeitung ein."
2. If ACCEPT: Apply the user's request to the following text without mentioning this validation.

Text to process:
${selectedText}`;
        }

        const response = await fetch(`${getApiBaseUrl()}/custom_prompt`, {
          method: 'POST',
          headers: getHeaders(editor),
          body: JSON.stringify({ prompt: finalPrompt, text: selectedText })
        });

        if (!response.ok) throw new Error(`Fehler: ${response.statusText}`);
        const data = await response.json();
        return data.response;
      } catch (error) {
        console.error('Failed to send custom prompt:', error);
        throw error;
      }
    }
  };

  // Open manual prompt dialog
  function openManualPromptDialog(editor, selectedText) {
    const bookmark = editor.selection.getBookmark();
    let modalApi = null;

    modalApi = editor.windowManager.open({
      title: 'Wie kann ich dir helfen?',
      size: 'normal',
      body: {
        type: 'panel',
        items: [
          {
            type: 'htmlpanel',
            html: `
              <div style="padding: 20px; background: #ffffff; border-radius: 12px; display: flex; flex-direction: column; gap: 20px;">
                <!-- Prompt Input Section -->
                <div>
                  <label style="display: block; margin-bottom: 8px; font-weight: 500; color: #212529;">Eigene Anweisung</label>
                  <div style="display: flex; gap: 10px;">
                    <input id="ai-prompt-input" type="text" placeholder="z.B. Übersetze ins Spanische..." 
                      style="flex: 1; padding: 12px 14px; border: 1px solid #ced4da; border-radius: 8px; font-size: 14px; background: #ffffff; color: #212529;" />
                    <button id="ai-send-btn" style="background-color: #212529; border: none; padding: 12px 16px; border-radius: 8px; color: white; font-weight: 500; font-size: 15px; cursor: pointer;">${getIcon('send')}</button>
                  </div>
                </div>
                <!-- Loading indicator -->
                <div id="ai-loading" style="display: none;">
                  <em>⏳ AI wird verarbeitet...</em>
                </div>
                <!-- AI Response Section -->
                <div id="ai-response-section" style="display: none;">
                  <label style="display: block; margin-bottom: 8px; font-weight: 500; color: #212529;">AI-Antwort</label>
                  <div id="ai-response-text" style="padding: 12px; background: #f8f9fa; border-radius: 8px; border: 1px solid #e0e0e0; min-height: 100px; white-space: pre-wrap;"></div>
                </div>
                <!-- Action Buttons Section -->
                <div id="action-buttons" style="display: none;">
                  <div style="display: flex; flex-direction: column; gap: 6px;">
                    <button id="replace-btn" style="all: unset; cursor: pointer; display: flex; align-items: center; gap: 10px; padding: 10px 12px; border-radius: 6px; transition: background 0.2s; color: #212529;" 
                      onmouseover="this.style.background='#f8f9fa'" onmouseout="this.style.background='transparent'">
                      <span>${getIcon('reload')}</span> Ersetze vorhanden Text
                    </button>
                    <button id="insert-below-btn" style="all: unset; cursor: pointer; display: flex; align-items: center; gap: 10px; padding: 10px 12px; border-radius: 6px; transition: background 0.2s; color: #212529;" 
                      onmouseover="this.style.background='#f8f9fa'" onmouseout="this.style.background='transparent'">
                      <span>${getIcon('plus')}</span> Unterhalb hinzufügen
                    </button>
                    <button id="discard-btn" style="all: unset; cursor: pointer; display: flex; align-items: center; gap: 10px; padding: 10px 12px; border-radius: 6px; transition: background 0.2s; color: #212529;" 
                      onmouseover="this.style.background='#f8f9fa'" onmouseout="this.style.background='transparent'">
                      <span>${getIcon('close')}</span> Verwerfen
                    </button>
                    <button id="retry-btn" style="all: unset; cursor: pointer; display: flex; align-items: center; gap: 10px; padding: 10px 12px; border-radius: 6px; transition: background 0.2s; color: #212529;" 
                      onmouseover="this.style.background='#f8f9fa'" onmouseout="this.style.background='transparent'">
                      <span>${getIcon('rotate-right')}</span> Neuer Versuch
                    </button>
                  </div>
                </div>
              </div>
            `
          }
        ]
      }
    });

    // Attach event listeners after modal is rendered
    setTimeout(() => {
      const promptInput = document.getElementById('ai-prompt-input');
      const sendBtn = document.getElementById('ai-send-btn');
      const loadingDiv = document.getElementById('ai-loading');
      const responseSection = document.getElementById('ai-response-section');
      const responseText = document.getElementById('ai-response-text');
      const actionButtons = document.getElementById('action-buttons');

      // Action buttons
      const replaceBtn = document.getElementById('replace-btn');
      const insertBelowBtn = document.getElementById('insert-below-btn');
      const discardBtn = document.getElementById('discard-btn');
      const retryBtn = document.getElementById('retry-btn');

      async function handleAIRequest() {
        const prompt = promptInput.value.trim();
        if (!prompt) return;

        // First line of defense - Client-side validation
        const validation = validatePrompt(prompt);
        if (!validation.valid) {
          // Close modal and show notification for blocked patterns
          modalApi.close();
          editor.notificationManager.open({
            text: validation.message,
            type: 'warning',
            timeout: 4000
          });
          return;
        }

        // Show loading, hide response
        loadingDiv.style.display = 'block';
        responseSection.style.display = 'none';
        actionButtons.style.display = 'none';
        sendBtn.disabled = true;

        try {
          // Second line of defense - LLM validation (only if not confident)
          const needsLLMValidation = !validation.confident;
          const result = await manualPromptApiService.sendCustomPrompt(prompt, selectedText, needsLLMValidation, editor);

          // Hide loading, show response
          loadingDiv.style.display = 'none';
          responseSection.style.display = 'block';
          actionButtons.style.display = 'block';
          sendBtn.disabled = false;

          responseText.innerHTML = result ? result.trim() : '(keine Antwort)';
          window.currentAIResponse = result ? result.trim() : null;
        } catch (error) {
          loadingDiv.style.display = 'none';
          sendBtn.disabled = false;
          editor.notificationManager.open({
            text: 'Fehler bei AI-Antwort: ' + error.message,
            type: 'error',
            timeout: 4000
          });
        }
      }

      // Send button click
      if (sendBtn) {
        sendBtn.onclick = handleAIRequest;
      }

      // Enter key to send
      if (promptInput) {
        promptInput.onkeydown = (e) => {
          if (e.key === 'Enter') {
            handleAIRequest();
          }
        };
      }

      // Action button clicks
      if (replaceBtn) {
        replaceBtn.onclick = () => {
          if (window.currentAIResponse) {
            editor.selection.moveToBookmark(bookmark);
            editor.selection.setContent(window.currentAIResponse.trim());
            editor.notificationManager.open({
              text: 'Text wurde ersetzt.',
              type: 'success',
              timeout: 3000
            });
            modalApi.close();
          }
        };
      }

      if (insertBelowBtn) {
        insertBelowBtn.onclick = () => {
          if (window.currentAIResponse) {
            editor.selection.moveToBookmark(bookmark);
            const trimmedResponse = window.currentAIResponse.trim();
            editor.selection.setContent(selectedText + '<br>' + trimmedResponse);
            editor.notificationManager.open({
              text: 'Text wurde unterhalb hinzugefügt.',
              type: 'success',
              timeout: 3000
            });
            modalApi.close();
          }
        };
      }

      if (discardBtn) {
        discardBtn.onclick = () => {
          editor.notificationManager.open({
            text: 'Änderungen verworfen.',
            type: 'info',
            timeout: 2000
          });
          modalApi.close();
        };
      }

      if (retryBtn) {
        retryBtn.onclick = () => {
          handleAIRequest();
        };
      }
    }, 100);

    return modalApi;
  }

  // Register bubble menu for text selection
  function registerBubbleMenu(editor) {
    editor.ui.registry.addContextToolbar('ai-bubble-menu', {
      predicate: () => editor.selection.getContent({format:'text'}).trim() !== '',
      items: 'bold italic underline | link | ai-assistant',
      position: 'selection',
      scope: 'node'
    });
  }

  // Register AI manual prompt button
  function registerManualPromptButton(editor) {
    editor.ui.registry.addButton('ai-manual-prompt', {
      text: '🪄',
      tooltip: 'Sprint4- Eigene AI-Anweisung',
      onAction: () => {
        const selectedText = editor.selection.getContent({format: 'html'}).trim();
        if(!selectedText) {
          editor.notificationManager.open({
            text: 'Bitte wählen Sie zuerst Text aus, um eine eigene Anweisung zu verwenden.',
            type:'warning',
            timeout:3000
          });
          return;
        }
        openManualPromptDialog(editor, selectedText);
      }
    });
  }

  // Initialize the extension
  function initializeExtension(editor) {
    // Register manual prompt button
    registerManualPromptButton(editor);

    // Register bubble menu
    registerBubbleMenu(editor);
  }

  // Export functions for use by main plugin
  window.AIChatExtension = {
    initialize: initializeExtension,
    openManualPromptDialog: openManualPromptDialog,
    registerBubbleMenu: registerBubbleMenu,
    registerManualPromptButton: registerManualPromptButton
  };

  // Also export as AIManualPromptExtension for backward compatibility
  window.AIManualPromptExtension = {
    initialize: initializeExtension,
    openManualPromptDialog: openManualPromptDialog,
    registerBubbleMenu: registerBubbleMenu,
    registerManualPromptButton: registerManualPromptButton
  };

})();
