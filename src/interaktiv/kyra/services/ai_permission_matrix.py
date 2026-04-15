"""REST API service for managing the group × feature permission matrix.

GET  @ai-permission-matrix → current matrix + available groups
POST @ai-permission-matrix → save matrix (updates Plone role→permission assignments)
"""

import json
import logging
import transaction

from plone import api

logger = logging.getLogger(__name__)
from plone.protect.interfaces import IDisableCSRFProtection
from plone.restapi.deserializer import json_body
from plone.restapi.services import Service
from zExceptions import BadRequest
from zope.interface import alsoProvides

# Maps frontend feature key → Plone permission title
FEATURE_PERMISSIONS = {
    "chat": "AIAssistant: Use Chat",
    "translate": "AIAssistant: Apply Actions",
    "manage_glossary": "AIAssistant: Manage Glossary",
    "manage_tag_mappings": "AIAssistant: Manage Tag Mappings",
    "manage_prompts": "AIAssistant: Manage Prompts",
    "manage_settings": "AIAssistant: Manage Settings",
    "assistant_run": "AIAssistant: Run Assistant",
}

# Roles that should never be editable via this UI
PROTECTED_ROLES = {"Manager"}


def _get_groups_with_roles():
    """Return a list of {id, title, roles} for all non-virtual groups."""
    acl = api.portal.get_tool("acl_users")
    results = []
    for group in acl.searchGroups():
        group_id = group["id"]
        if group_id == "AuthenticatedUsers":
            continue
        group_obj = acl.getGroupById(group_id)
        if group_obj is None:
            continue
        roles = set(group_obj.getRoles()) - {"Authenticated"}
        results.append({
            "id": group_id,
            "title": group_obj.getProperty("title", group_id) or group_id,
            "roles": sorted(roles),
        })
    return results


def _read_matrix(portal):
    """Read the current permission→role assignments from the portal."""
    matrix = {}
    for feature_key, perm_title in FEATURE_PERMISSIONS.items():
        roles_for_perm = portal.rolesOfPermission(perm_title)
        active_roles = {
            r["name"] for r in roles_for_perm if r["selected"]
        }
        matrix[feature_key] = active_roles
    return matrix


def _write_matrix(portal, matrix, groups):
    """Write the permission→role assignments to the portal.

    matrix: {feature_key: [group_id, ...]}
    groups: result of _get_groups_with_roles()
    """
    group_roles = {}
    for g in groups:
        group_roles[g["id"]] = set(g["roles"])

    for feature_key, perm_title in FEATURE_PERMISSIONS.items():
        granted_group_ids = set(matrix.get(feature_key, []))

        new_roles = set()
        for g in groups:
            if g["id"] in granted_group_ids:
                new_roles.update(group_roles[g["id"]])

        new_roles.update(PROTECTED_ROLES)

        portal.manage_permission(
            perm_title,
            roles=sorted(new_roles),
            acquire=0,
        )


class AIPermissionMatrixGet(Service):
    """GET @ai-permission-matrix"""

    def __init__(self, context, request):
        super().__init__(context, request)
        alsoProvides(self.request, IDisableCSRFProtection)

    def reply(self):
        portal = api.portal.get()
        groups = _get_groups_with_roles()
        role_matrix = _read_matrix(portal)

        group_roles = {}
        for g in groups:
            group_roles[g["id"]] = set(g["roles"])

        group_matrix = {}
        for feature_key, active_roles in role_matrix.items():
            enabled_groups = []
            for g in groups:
                if group_roles[g["id"]] & active_roles:
                    enabled_groups.append(g["id"])
            group_matrix[feature_key] = enabled_groups

        return {
            "groups": [
                {"id": g["id"], "title": g["title"]}
                for g in groups
            ],
            "features": list(FEATURE_PERMISSIONS.keys()),
            "matrix": group_matrix,
        }


class AIPermissionMatrixPost(Service):
    """POST @ai-permission-matrix"""

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
        groups = _get_groups_with_roles()
        logger.info("[KYRA PERMISSIONS] Saving matrix: %s", matrix)
        logger.info("[KYRA PERMISSIONS] Groups: %s", [(g['id'], g['roles']) for g in groups])
        _write_matrix(portal, matrix, groups)
        transaction.commit()
        logger.info("[KYRA PERMISSIONS] Saved and committed")

        return {"status": "ok"}
