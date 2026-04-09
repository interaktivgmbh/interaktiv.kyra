==================
interaktiv.kyra
==================

Kyra AI Assistant — Plone backend addon for the Volto-based AI content editing suite.

Version: 2.2.5

Features
--------

- **External Layout Agent Proxy** — Proxies all AI chat and edit requests to an external Layout Agent backend via REST API, with Keycloak authentication
- **Site Context Pre-loading** — Loads the site tree and extracts document content (including PDF text page-by-page) from the Plone catalog, injected into the first message so the agent knows the full site structure
- **Callback Endpoints** — 8 REST endpoints that the external Layout Agent can call back to query Plone data (pages, search, documents, images, breadcrumbs)
- **DeepL Translation** — Page and subtree translation with glossary support and batch processing
- **AI Chat** — Chat via AI Gateway with Keycloak authentication
- **Prompt Management** — Server-side prompt storage with file attachments
- **Permission Matrix** — Fine-grained role-based access control per AI feature
- **Chat History** — Server-side per-user conversation storage via IAnnotations
- **Auto Error Reporting** — Automatic GitHub issue creation from AI errors
- **Glossary & Tag Mappings** — DeepL glossary sync and keyword translation mappings
- **Audit Logging** — Server-side logging of all AI actions

Architecture
------------

The Layout Agent runs as an **external service** (separate FastAPI backend). Plone acts as a proxy:

1. **Frontend → Plone**: Receives requests, adds site context (pages, PDF content), forwards to the external backend
2. **Plone → Layout Agent**: Pure pass-through proxy with Keycloak authentication
3. **Layout Agent → Plone** (Callbacks): When the Layout Agent can reach Plone (e.g. in production), it can query Plone for additional data via callback endpoints

Chat mode uses ``permissions: []`` (read-only). Edit mode uses full permissions (``update``, ``create``, ``delete``, ``move``).

Configuration: Set ``Backend URL`` in the Kyra control panel to point to the external Layout Agent.

REST Endpoints
--------------

.. list-table::
   :header-rows: 1

   * - Endpoint
     - Description
   * - ``@ai-edit-conversations``
     - Create layout agent conversation (edit mode, full permissions)
   * - ``@ai-edit-messages``
     - Send layout edit instruction
   * - ``@ai-edit-jobs``
     - Poll layout job status (with live preview)
   * - ``@ai-edit-job-cancel``
     - Cancel running layout job
   * - ``@ai-chat-conversations``
     - Create layout agent conversation (chat mode, read-only)
   * - ``@ai-chat-messages``
     - Send chat message to layout agent
   * - ``@ai-chat-jobs``
     - Poll chat job status
   * - ``@ai-chat-job-cancel``
     - Cancel running chat job
   * - ``@ai-callback-page``
     - Callback: return Volto page state for a path
   * - ``@ai-callback-metadata``
     - Callback: return page metadata
   * - ``@ai-callback-children``
     - Callback: list direct children
   * - ``@ai-callback-search``
     - Callback: search content
   * - ``@ai-callback-breadcrumb``
     - Callback: return breadcrumb/ancestors
   * - ``@ai-callback-documents-search``
     - Callback: search documents
   * - ``@ai-callback-documents-read``
     - Callback: read document pages
   * - ``@ai-callback-image``
     - Callback: return image URL
   * - ``@ai-chat``
     - AI chat messaging (legacy gateway)
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

Dependencies include ``requests``, ``deepl``, ``PyPDF2``, and others (see ``setup.py``).

Note: ``langchain``, ``langchain-openai``, and ``langgraph`` are no longer required — the AI agent runs externally.

License
-------

GPL version 2

Copyright
---------

Interaktiv GmbH
