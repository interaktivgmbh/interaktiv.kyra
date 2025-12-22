from datetime import datetime
from typing import Any, Dict, List, Optional

from interaktiv.kyra import logger
from plone import api
from zope.annotation.interfaces import IAnnotations

AUDIT_KEY = "interaktiv.kyra.ai_actions_audit"
AUDIT_LIMIT = 200


def log_ai_action(obj, actions: List[Dict[str, Any]], plan_id: Optional[str] = None) -> None:
    portal = api.portal.get()
    user = api.user.get_current()
    entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "user_id": user.getId() if user else None,
        "path": "/".join(obj.getPhysicalPath()) if obj is not None else "",
        "plan_id": plan_id,
        "actions": actions,
    }

    annotations = IAnnotations(portal)
    audit_log = annotations.get(AUDIT_KEY)
    if audit_log is None:
        audit_log = []
        annotations[AUDIT_KEY] = audit_log

    audit_log.append(entry)
    if len(audit_log) > AUDIT_LIMIT:
        del audit_log[:-AUDIT_LIMIT]

    logger.info(f"[KYRA AI] Actions applied: {entry}")
