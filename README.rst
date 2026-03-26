==================
interaktiv.kyra
==================

Kyra AI Assistant — Plone backend addon for the Volto-based AI content editing suite.

Features
--------

- **Integrated Layout Agent** — AI-driven page layout generation and editing powered by LangGraph, running directly inside Plone (no external service required)
- **DeepL Translation** — Page and subtree translation with glossary support and batch processing
- **AI Chat** — Streaming chat via AI Gateway with Keycloak authentication
- **Prompt Management** — Server-side prompt storage with file attachments
- **Permission Matrix** — Fine-grained role-based access control per AI feature
- **Chat History** — Server-side per-user conversation storage via IAnnotations
- **Auto Error Reporting** — Automatic GitHub issue creation from AI errors
- **Glossary & Tag Mappings** — DeepL glossary sync and keyword translation mappings
- **Audit Logging** — Server-side logging of all AI actions

Layout Agent
------------

The Layout Agent (originally developed as an external service) is vendored as ``interaktiv.kyra.agent`` and runs in-process. It provides:

- Full Volto JSON to Block IR conversion (and diff-aware reverse conversion)
- ~50 LangChain tools for block CRUD across 20 block types
- Stock photo search (Pexels/Unsplash)
- Permission-based tool sets (read-only, update-only, full access)
- System prompts with German design guidelines
- Async execution via a dedicated ``asyncio`` event loop in a daemon thread

Configuration: Set ``OpenAI API Key`` and ``Layout Agent LLM Model`` in the Kyra control panel.

REST Endpoints
--------------

.. list-table::
   :header-rows: 1

   * - Endpoint
     - Description
   * - ``@ai-edit-conversations``
     - Create layout agent conversation
   * - ``@ai-edit-messages``
     - Send layout edit instruction
   * - ``@ai-edit-jobs``
     - Poll layout job status (with live preview)
   * - ``@ai-edit-job-cancel``
     - Cancel running layout job
   * - ``@ai-chat``
     - AI chat messaging
   * - ``@ai-chat-history``
     - Per-user conversation storage
   * - ``@ai-chat-upload``
     - File upload for chat context
   * - ``@ai-actions``
     - Translation and content actions
   * - ``@ai-capabilities``
     - Feature flags and permissions
   * - ``@ai-prompts``
     - Prompt CRUD
   * - ``@ai-prompt-files``
     - Prompt file attachments
   * - ``@ai-assistant-run``
     - Execute prompt against text
   * - ``@ai-glossary``
     - DeepL glossary management
   * - ``@ai-tag-mappings``
     - Keyword translation mappings
   * - ``@ai-permission-matrix``
     - Role-based permission matrix
   * - ``@ai-error-report``
     - Auto-create GitHub issues from errors

Installation
------------

Add ``interaktiv.kyra`` to your Plone 6 project. Requires Python 3.11+.

Dependencies include ``langchain``, ``langchain-openai``, ``langgraph``, ``deepl``, ``pdfminer.six``, and others (see ``setup.py``).

License
-------

GPL version 2

Copyright
---------

Interaktiv GmbH
