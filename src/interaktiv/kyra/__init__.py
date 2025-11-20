import logging

from zope.i18nmessageid import MessageFactory

project_name = 'interaktiv.kyra'

# noinspection PyPep8Naming
_ = MessageFactory(project_name)

logger = logging.getLogger(project_name)
