==================
interaktiv.kyra
==================

KYRA AI Assistant Integration for Plone
========================================

A Plone add-on that integrates the KYRA AI assistant service, providing AI-powered content generation, editing, and management capabilities through prompts and file operations.

.. contents:: Table of Contents
   :depth: 3
   :local:

Features
--------

- **AI-Powered Prompts**: Create, manage, and apply AI prompts for content generation
- **TinyMCE Integration**: Rich text editor plugin with AI assistant menu
- **File Management**: Upload, download, and manage files associated with prompts
- **REST API**: REST API for prompt and file operations
- **Keycloak Authentication**: Secure OAuth2 authentication via Keycloak
- **Configurable**: Easy configuration through Plone control panel
- **Browser Layer**: Proper Plone integration with browser layer support
- **Multilingual**: Translation support for multiple languages

Installation
------------

Add interaktiv.kyra to your buildout::

    [buildout]
    ...
    eggs =
        interaktiv.kyra

Run buildout::

    bin/buildout

Install the add-on in Plone:

1. Go to Site Setup → Add-ons
2. Install "interaktiv.kyra"

Configuration
-------------

After installation, configure the KYRA integration:

1. Navigate to **Controlpanel → KYRA AI Assistant**
2. Configure the following settings:

   - **Gateway URL**: URL of the KYRA gateway API (e.g., ``https://api.kyra.example.com/prompts``)
   - **Keycloak Realms URL**: Keycloak authentication endpoint (e.g., ``https://auth.example.com/realms/kyra``)
   - **Keycloak Client ID**: OAuth2 client identifier
   - **Keycloak Client Secret**: OAuth2 client secret (stored securely)
   - **Domain ID**: Domain identifier for multi-tenant environments (default: ``plone``)

Architecture
------------

API Structure
~~~~~~~~~~~~~

The package follows a clean architecture with three main layers:

1. **API Client Layer** (`interaktiv.kyra.api`): Core API client for KYRA service
2. **Service Layer** (`interaktiv.kyra.services`): Plone REST API endpoints
3. **Registry Layer** (`interaktiv.kyra.registry`): Configuration management

Main Entry Point: KyraAPI
~~~~~~~~~~~~~~~~~~~~~~~~~~

The `KyraAPI <psi_element://interaktiv.kyra.api.KyraAPI>`_ class is the central entry point for all KYRA operations::

    from interaktiv.kyra.api import KyraAPI

    # Initialize the API client
    kyra = KyraAPI()

    # Access prompt operations
    prompts = kyra.prompts.list()

    # Access file operations
    files = kyra.files.get(prompt_id='123')

The `KyraAPI <psi_element://interaktiv.kyra.api.KyraAPI>`_ provides two main interfaces:

- **kyra.prompts**: Prompt management (`Prompts <psi_element://interaktiv.kyra.api.prompts.Prompts>`_)
- **kyra.files**: File operations (`Files <psi_element://interaktiv.kyra.api.files.Files>`_)

TinyMCE Integration
-------------------

The package provides a TinyMCE plugin that integrates AI assistant capabilities directly into the rich text editor.

Features
~~~~~~~~

- **Dynamic Menu**: AI assistant menu populated from available prompts
- **Category Organization**: Prompts grouped by categories in nested menus
- **Visual Feedback**: Loading indicators and animations during AI processing
- **Success/Error Notifications**: User-friendly feedback messages
- **Text Selection**: Apply prompts to selected text in the editor
- **Multilingual Support**: Translatable UI elements

Plugin Registration
~~~~~~~~~~~~~~~~~~~

The plugin is automatically registered when the add-on is installed. It's loaded via the TinyMCE configuration in Plone.

Using the TinyMCE Plugin
~~~~~~~~~~~~~~~~~~~~~~~~~

**For Content Editors:**

1. Select text in the TinyMCE editor
2. Click the **"AI"** button in the toolbar
3. Choose a prompt from the categorized menu
4. Wait for the AI to process your request
5. The selected text will be replaced (or appended to) with the AI-generated content

Usage Guide
-----------

Python API Usage
~~~~~~~~~~~~~~~~

Working with Prompts
^^^^^^^^^^^^^^^^^^^^

**List all prompts**::

    from interaktiv.kyra.api import KyraAPI

    kyra = KyraAPI()

    # Get paginated list of prompts
    response = kyra.prompts.list(page=1, size=50)
    prompts = response.get('prompts', [])

    for prompt in prompts:
        print(f"ID: {prompt['id']}, Name: {prompt['name']}")

**Get a specific prompt**::

    prompt = kyra.prompts.get(prompt_id='abc123')
    print(f"Prompt: {prompt['prompt']}")
    print(f"Description: {prompt['description']}")

**Create a new prompt**::

    from interaktiv.kyra.api.types import PromptData

    payload: PromptData = {
        'name': 'Content Summarizer',
        'description': 'Summarizes long content into key points',
        'prompt': 'Summarize the following text in 3-5 bullet points:\n\n{text}',
        'metadata': {
            'categories': ['content', 'summarization'],
            'action': 'summarize'
        }
    }

    result = kyra.prompts.create(payload)
    prompt_id = result.get('id')

**Update an existing prompt**::

    from interaktiv.kyra.api.types import PromptData

    updates: PromptData = {
        'name': 'Enhanced Content Summarizer',
        'description': 'Updated description',
        'prompt': 'Provide a detailed summary...',
        'metadata': {
            'categories': ['content', 'summarization', 'analysis'],
            'action': 'summarize'
        }
    }

    result = kyra.prompts.update(prompt_id='abc123', payload=updates)

**Apply a prompt** (Generate AI content)::

    from interaktiv.kyra.api.types import InstructionData

    instruction: InstructionData = {
        'query': 'Make this more engaging',
        'text': 'Your original content here...',
        'useContext': True
    }

    result = kyra.prompts.apply(prompt_id='abc123', payload=instruction)
    ai_response = result.get('response')

**Delete a prompt**::

    result = kyra.prompts.delete(prompt_id='abc123')

Working with Files
^^^^^^^^^^^^^^^^^^

**List files for a prompt**::

    files = kyra.files.get(prompt_id='abc123')

    for file in files:
        print(f"File: {file['filename']}, Size: {file['size']}")

**Upload a file**::

    # In a view or service with access to request
    file_upload = request.form.get('file')

    result = kyra.files.upload(
        prompt_id='abc123',
        file_field=file_upload
    )

**Upload multiple files**::

    # Multiple files from form
    files = request.form.get('files')  # List of FileUpload objects

    result = kyra.files.upload(
        prompt_id='abc123',
        file_field=files
    )

**Download a file**::

    result = kyra.files.download(
        prompt_id='abc123',
        file_id='file456'
    )

    file_content = result.get('content')  # Binary content

**Delete a file**::

    result = kyra.files.delete(
        prompt_id='abc123',
        file_id='file456'
    )

REST API Endpoints
~~~~~~~~~~~~~~~~~~

The package provides REST API endpoints for external integrations.

GET /@prompts
^^^^^^^^^^^^^

Retrieve paginated list of prompts.

**Query Parameters:**

- ``page`` (int, optional): Page number (default: 1)
- ``size`` (int, optional): Items per page (default: 100, max: 100)

**Example Request**::

    GET /Plone/@prompts?page=1&size=20
    Accept: application/json
    Authorization: Bearer <token>

**Example Response**::

    {
      "prompts": [
        {
          "id": "abc123",
          "name": "Content Summarizer",
          "description": "Summarizes content",
          "prompt": "Summarize: {text}",
          "metadata": {
            "categories": ["content"],
            "action": "summarize"
          }
        }
      ],
      "total": 42,
      "page": 1,
      "size": 20
    }

POST /@prompts/{prompt_id}
^^^^^^^^^^^^^^^^^^^^^^^^^^^

Apply a prompt to generate AI content.

**Path Parameters:**

- ``prompt_id`` (string): Unique identifier of the prompt

**Request Body**::

    {
      "text": "Your content to process...",
      "query": "Make this more professional",
      "include_context": true
    }

**Example Request**::

    POST /Plone/@prompts/abc123
    Content-Type: application/json
    Authorization: Bearer <token>

    {
      "text": "This is my draft content.",
      "query": "Make it more engaging",
      "include_context": true
    }

**Example Response**::

    {
      "response": "Here is the enhanced, more engaging version..."
    }

POST /@custom_prompt
^^^^^^^^^^^^^^^^^^^^

Execute a custom prompt without saving it.

**Request Body**::

    {
      "prompt": "Your custom prompt template...",
      "text": "Content to process..."
    }

**Example Request**::

    POST /Plone/@custom_prompt
    Content-Type: application/json
    Authorization: Bearer <token>

    {
      "prompt": "Rewrite this in a friendly tone: {text}",
      "text": "The system has encountered an error."
    }


Development
-----------

Setup Development Environment
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. Clone the repository::

    cd src
    git clone <repository-url> interaktiv.kyra

2. Run buildout::

    bin/buildout

3. Start Plone::

    bin/instance fg

Running Tests
~~~~~~~~~~~~~

Run the test suite::

    bin/test -s interaktiv.kyra

Run tests with coverage::

    bin/coverage run bin/test -s interaktiv.kyra
    bin/coverage report
    bin/coverage html

Code Structure
~~~~~~~~~~~~~~

::

    src/interaktiv/kyra/
    ├── api/                    # API client layer
    │   ├── __init__.py        # KyraAPI main class
    │   ├── base.py            # APIBase with auth & requests
    │   ├── prompts.py         # Prompts client
    │   ├── files.py           # Files client
    │   └── types.py           # TypedDict definitions
    ├── services/              # REST API endpoints
    │   ├── base.py            # ServiceBase
    │   ├── prompts.py         # Prompt services
    │   └── configure.zcml     # Service registration
    ├── components/            # Frontend components
    │   └── js/
    │       └── ai-assistant-plugin.js  # TinyMCE plugin
    ├── registry/              # Configuration schemas
    │   └── ai_assistant.py    # Settings interface
    ├── controlpanels/         # Control panel
    ├── views/                 # Browser views
    ├── static/                # Static resources
    └── locales/               # Translations

Contributing
~~~~~~~~~~~~

1. Follow PEP 8 style guidelines
2. Add docstrings to all public classes and methods
3. Include type hints for function signatures
4. Write tests for new features
5. Update documentation

Permissions
-----------

The package defines three permissions:

- ``interaktiv.kyra.prompts.get``: View prompts
- ``interaktiv.kyra.prompts.post``: Create/apply prompts
- ``interaktiv.kyra.manage.settings``: Controlpanel settings

Troubleshooting
---------------

Authentication Issues
~~~~~~~~~~~~~~~~~~~~~

If you encounter authentication errors:

1. Verify Keycloak credentials in control panel
2. Check that gateway URL is accessible
3. Ensure client has necessary permissions in Keycloak
4. Review Plone logs for detailed error messages

Connection Timeouts
~~~~~~~~~~~~~~~~~~~

Default timeout is 30 seconds. For long-running operations, consider:

- Increasing timeout in `APIBase.request() <psi_element://interaktiv.kyra.api.base.APIBase#request>`_ method
- Implementing async/background task processing
- Using progress indicators in UI

API Responses
~~~~~~~~~~~~~

All API methods return dictionaries. Always check for ``'error'`` key::

    result = kyra.prompts.list()

    if 'error' in result:
        # Handle error
        logger.error(f"API error: {result['error']}")
        return

    # Process successful response
    prompts = result.get('prompts', [])

Further Resources
-----------------

- **KYRA Documentation**: `<https://kyra-docs.example.com>`_ (update with actual URL)
- **Plone Documentation**: `<https://docs.plone.org>`_
- **REST API Guide**: `<https://plonerestapi.readthedocs.io>`_

License
-------

GPL version 2

Copyright
---------

© 2025 Interaktiv GmbH

All rights reserved.

Support
-------

For issues:

- Create an issue on the project repository

Changelog
---------

1.0.0 (Unreleased)
~~~~~~~~~~~~~~~~~~

- Initial release
- KYRA API integration
- Prompt management (CRUD operations)
- File operations (upload, download, delete)
- REST API endpoints
- Keycloak authentication
- Control panel configuration
- Type-safe API with TypedDict support
