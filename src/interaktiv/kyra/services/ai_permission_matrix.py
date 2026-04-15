import json
import logging

from persistent.mapping import PersistentMapping
from persistent.list import PersistentList
from plone import api
from plone.protect.interfaces import IDisableCSRFProtection
from plone.restapi.deserializer import json_body
from plone.restapi.services import Service
from zExceptions import BadRequest
from zope.annotation.interfaces import IAnnotations
from zope.interface import alsoProvides

logger = logging.getLogger(__name__)

ANNOTATION_KEY = "interaktiv.kyra.permission_matrix"

FEATURE_PERMISSIONS = {
    "chat": "AIAssistant: Use Chat",
    "translate": "AIAssistant: Apply Actions",
    "manage_glossary": "AIAssistant: Manage Glossary",
    "manage_tag_mappings": "AIAssistant: Manage Tag Mappings",
    "manage_prompts": "AIAssistant: Manage Prompts",
    "manage_settings": "AIAssistant: Manage Settings",
    "assistant_run": "AIAssistant: Run Assistant",
}


def _get_groups():
    acl = api.portal.get_tool("acl_users")
    results = []
    for group in acl.searchGroups():
        group_id = group["id"]
        if group_id == "AuthenticatedUsers":
            continue
        group_obj = acl.getGroupById(group_id)
        if group_obj is None:
            continue
        results.append({
            "id": group_id,
            "title": group_obj.getProperty("title", group_id) or group_id,
        })
    return results


def _read_matrix(portal):
    annotations = IAnnotations(portal)
    stored = annotations.get(ANNOTATION_KEY)
    if stored:
        return {k: list(v) for k, v in stored.items()}
    return {feature: [] for feature in FEATURE_PERMISSIONS}


def _write_matrix(portal, matrix):
    annotations = IAnnotations(portal)
    persistent = PersistentMapping()
    for key, groups in matrix.items():
        persistent[key] = PersistentList(groups if isinstance(groups, list) else list(groups))
    annotations[ANNOTATION_KEY] = persistent


def get_user_features(context=None):
    """Return the list of features the current user has access to."""
    if api.user.is_anonymous():
        return []

    portal = api.portal.get()
    matrix = _read_matrix(portal)
    user = api.user.get_current()
    user_groups = set(user.getGroups())

    features = []
    for feature, granted_groups in matrix.items():
        if user_groups & set(granted_groups):
            features.append(feature)

    if "Manager" in (user.getRoles() or []):
        features = list(FEATURE_PERMISSIONS.keys())

    return features


class AIPermissionMatrixGet(Service):

    def __init__(self, context, request):
        super().__init__(context, request)
        alsoProvides(self.request, IDisableCSRFProtection)

    def reply(self):
        portal = api.portal.get()
        groups = _get_groups()
        matrix = _read_matrix(portal)

        return {
            "groups": [{"id": g["id"], "title": g["title"]} for g in groups],
            "features": list(FEATURE_PERMISSIONS.keys()),
            "matrix": matrix,
        }


class AIPermissionMatrixPost(Service):

    def __init__(self, context, request):
        super().__init__(context, request)
        alsoProvides(self.request, IDisableCSRFProtection)

    def reply(self):
        data = json_body(self.request)
        if not isinstance(data, dict) or "matrix" not in data:
            raise BadRequest("Expected JSON with 'matrix' key")

        matrix = data["matrix"]
        if not isinstance(matrix, dict):
            raise BadRequest("'matrix' must be an object")

        portal = api.portal.get()
        _write_matrix(portal, matrix)
        logger.info("[KYRA PERMISSIONS] Matrix saved: %s", matrix)

        return {"status": "ok"}
