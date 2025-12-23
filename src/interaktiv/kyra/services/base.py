from interaktiv.kyra.api import KyraAPI

from plone.protect.interfaces import IDisableCSRFProtection
from plone.restapi.services import Service
from zope.interface import alsoProvides


class ServiceBase(Service):
    kyra: KyraAPI

    def __init__(self, context, request):
        self.context = context
        self.request = request
        alsoProvides(self.request, IDisableCSRFProtection)

        self.kyra = KyraAPI()
