==================
interaktiv.kyra
==================

Plone backend addon for `Kyra — AI Assistant for Volto <https://github.com/interaktivgmbh/volto-interaktiv-kyra>`_.

Provides REST API endpoints for DeepL translation, AI chat, prompt management, glossary and tag mappings.

For full documentation, features and screenshots see the `frontend README <https://github.com/interaktivgmbh/volto-interaktiv-kyra#readme>`_.

Installation
------------

Cookiecutter/Cookieplone project
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Add to your ``mx.ini``::

    [interaktiv.kyra]
    url = https://github.com/interaktivgmbh/interaktiv.kyra.git
    extras = test
    branch = v1.0

Then run::

    make install

Buildout
~~~~~~~~

Add to your ``buildout.cfg``::

    [buildout]
    ...
    eggs =
        interaktiv.kyra

Then run::

    bin/buildout

Configuration
-------------

Navigate to **Site Setup → Kyra AI Settings** and configure:

- ``gateway_url`` — AI gateway endpoint
- ``keycloak_realms_url`` — Keycloak auth URL
- ``keycloak_client_id`` — OAuth client ID
- ``keycloak_client_secret`` — OAuth client secret
- ``domain_id`` — Domain identifier (default: ``plone``)
- ``deepl_api_key`` — DeepL API key for translations

Testing
-------

Run tests with::

    bin/test -s interaktiv.kyra

License
-------

GPL version 2

Copyright
---------

`Interaktiv GmbH <https://www.interaktiv.de>`_
