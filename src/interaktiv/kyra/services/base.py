from interaktiv.kyra.api import KyraAPI
from plone.restapi.services import Service


class ServiceBase(Service):
    kyra: KyraAPI

    def __init__(self, context, request):
        super().__init__(context, request)
        self.kyra = KyraAPI()
