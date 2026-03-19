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


def _check_permission(permission_title, context) -> bool:
    """Check a single permission against the current user."""
    if context is None:
        return False
    return bool(api.user.has_permission(permission_title, obj=context))


def _capabilities_for(context) -> dict:
    is_anonymous = api.user.is_anonymous()
    can_edit = False

    if not is_anonymous and context is not None:
        can_edit = api.user.has_permission("Modify portal content", obj=context)

    features = ["chat"]
    if can_edit:
        features.extend(["actions_plan", "actions_apply"])

    # Fine-grained per-feature permissions (checked against Plone's
    # native permission→role assignments, configurable via the
    # @ai-permission-matrix endpoint)
    if is_anonymous:
        permissions = {
            "chat": False,
            "translate": False,
            "manage_glossary": False,
            "manage_tag_mappings": False,
            "manage_prompts": False,
            "manage_settings": False,
            "assistant_run": False,
        }
    else:
        permissions = {
            "chat": _check_permission("AIAssistant: Use Chat", context),
            "translate": _check_permission("AIAssistant: Apply Actions", context),
            "manage_glossary": _check_permission("AIAssistant: Manage Glossary", context),
            "manage_tag_mappings": _check_permission("AIAssistant: Manage Tag Mappings", context),
            "manage_prompts": _check_permission("AIAssistant: Manage Prompts", context),
            "manage_settings": _check_permission("AIAssistant: Manage Settings", context),
            "assistant_run": _check_permission("AIAssistant: Run Assistant", context),
        }

    is_admin = (
        not is_anonymous
        and context is not None
        and bool(api.user.has_permission("Manage portal", obj=context))
    )

    result = {
        "is_anonymous": is_anonymous,
        "can_edit": can_edit,
        "is_admin": is_admin,
        "features": features,
        "permissions": permissions,
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
    """GET /++api++/@ai-capabilities

    Does NOT require KyraAPI / gateway credentials — only checks local
    Plone permissions so it works even before the add-on is fully configured.
    """

    def __init__(self, context, request):
        super().__init__(context, request)
        alsoProvides(self.request, IDisableCSRFProtection)

    def reply(self):
        context = _resolve_context(self.context, self.request)
        if context is None:
            context = api.portal.get()
        return _capabilities_for(context)
