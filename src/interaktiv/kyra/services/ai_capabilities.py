from plone import api
from plone.base.interfaces import IPloneSiteRoot
from plone.protect.interfaces import IDisableCSRFProtection
from plone.restapi.services import Service
from zope.interface import alsoProvides


def _resolve_context(context, request):
    if context is not None and not IPloneSiteRoot.providedBy(context):
        return context

    value = request.get("context") if hasattr(request, "get") else None
    if not value:
        return None

    if isinstance(value, str):
        portal = api.portal.get()
        portal_url = portal.absolute_url()
        if value.startswith("http"):
            if value.startswith(portal_url):
                path = value[len(portal_url) :].lstrip("/")
                return api.content.get(path=path)
        if value.startswith("/"):
            return api.content.get(path=value.lstrip("/"))
        return api.content.get(UID=value)

    return None


def _capabilities_for(context) -> dict:
    is_anonymous = api.user.is_anonymous()
    can_edit = False

    if not is_anonymous and context is not None:
        can_edit = api.user.has_permission("Modify portal content", obj=context)

    features = ["chat"]
    if can_edit:
        features.extend(["actions_plan", "actions_apply"])

    result = {
        "is_anonymous": is_anonymous,
        "can_edit": can_edit,
        "features": features,
    }

    if can_edit:
        try:
            edit_url = api.portal.get_registry_record(
                "interaktiv.kyra.registry.ai_assistant.IAIAssistantSchema.edit_backend_url",
                default="",
            )
            if edit_url:
                result["edit_backend_url"] = "proxy"
        except Exception:
            pass

    return result


class AICapabilities(Service):

    def __init__(self, context, request):
        super().__init__(context, request)
        alsoProvides(self.request, IDisableCSRFProtection)

    def reply(self):
        context = _resolve_context(self.context, self.request)
        return _capabilities_for(context)
