import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from AccessControl import Unauthorized
from interaktiv.kyra import logger
from interaktiv.kyra.services.ai_translation import _apply_translation
from interaktiv.kyra.services.audit import log_ai_action
from interaktiv.kyra.services.base import ServiceBase
from plone import api
from plone.base.interfaces import IPloneSiteRoot
from plone.restapi.deserializer import json_body
from plone.restapi.services import Service
from zExceptions import BadRequest
from zope.annotation.interfaces import IAnnotations
from zope.interface import implementer
from zope.publisher.interfaces import IPublishTraverse

PLAN_STORAGE_KEY = "interaktiv.kyra.ai_actions_plans"
TRANSLATION_MAX_CONCURRENCY_DEFAULT = 3
TRANSLATION_TIMEOUT_DEFAULT = 60
TRANSLATION_RETRIES_DEFAULT = 2

ALLOWLIST = {"translate_content"}


def _get_int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, "").strip())
        return value if value > 0 else default
    except Exception:
        return default


def _max_translation_concurrency() -> int:
    return _get_int_env("KYRA_TRANSLATE_MAX_CONCURRENCY", TRANSLATION_MAX_CONCURRENCY_DEFAULT)


def _translation_timeout_seconds() -> int:
    return _get_int_env("KYRA_TRANSLATE_TIMEOUT", TRANSLATION_TIMEOUT_DEFAULT)


def _translation_retries() -> int:
    return _get_int_env("KYRA_TRANSLATE_RETRIES", TRANSLATION_RETRIES_DEFAULT)


def _derive_actions(translate_opts: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    target_lang = translate_opts.get("target_language") if translate_opts else None
    mode = translate_opts.get("mode") if translate_opts else None
    overwrite = bool(translate_opts.get("overwrite")) if translate_opts else False
    incremental = bool(translate_opts.get("incremental")) if translate_opts else False

    payload = {
        "target_language": target_lang or "en",
        "mode": mode or "single",
        "overwrite": overwrite,
    }
    if incremental:
        payload["incremental"] = True

    return [
        {
            "type": "translate_content",
            "payload": payload,
        }
    ]


def _preview_from_actions(actions: List[Dict[str, Any]]) -> Dict[str, Any]:
    summaries = []
    diffs = []
    for action in actions:
        action_type = action.get("type")
        payload = action.get("payload") or {}
        if action_type == "translate_content":
            summaries.append("Translate content")
            diffs.append(
                f"+ translate -> {payload.get('target_language')} ({payload.get('mode', 'single')})"
            )

    return {
        "summary": ", ".join(summaries) if summaries else "No changes proposed",
        "diff": "\n".join(diffs),
        "human_steps": summaries,
    }


def _build_translation_stub(actions: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for action in actions:
        if action.get("type") == "translate_content":
            payload = action.get("payload") or {}
            return {
                "target_language": payload.get("target_language"),
                "mode": payload.get("mode", "single"),
                "overwrite": bool(payload.get("overwrite")),
            }
    return None


def _resolve_target(context, data: Dict[str, Any]):
    if context is not None and not IPloneSiteRoot.providedBy(context):
        return context

    page = data.get("page") or {}
    uid = page.get("uid")
    url = page.get("url")

    if uid:
        obj = api.content.get(UID=uid)
        if obj is not None:
            return obj

    if url and isinstance(url, str):
        portal = api.portal.get()
        portal_url = portal.absolute_url()
        if url.startswith("http") and url.startswith(portal_url):
            url = url[len(portal_url):]
        if url.startswith("/"):
            return api.content.get(path=url.lstrip("/"))

    return None


def _ensure_editor(obj):
    if api.user.is_anonymous():
        raise Unauthorized("Login required")
    if obj is None:
        raise BadRequest("Missing target page")
    if not api.user.has_permission("Modify portal content", obj=obj):
        raise Unauthorized("Insufficient permissions")


def _apply_actions(obj, actions: List[Dict[str, Any]]) -> List[str]:
    changed: List[str] = []
    translation_report: Optional[Dict[str, Any]] = None
    for action in actions:
        action_type = action.get("type")
        payload = action.get("payload") or {}
        if action_type not in ALLOWLIST:
            raise BadRequest(f"Action type '{action_type}' is not allowed")

        if action_type == "translate_content":
            translation_report = _apply_translation(obj, payload)
            changed.append("translation")

    obj.reindexObject()
    if translation_report is not None:
        obj._v_last_translation_report = translation_report
    return changed


def _store_plan(obj, plan_id: str, actions: List[Dict[str, Any]], user_id: str) -> None:
    annotations = IAnnotations(obj)
    plans = annotations.get(PLAN_STORAGE_KEY)
    if plans is None:
        plans = {}
        annotations[PLAN_STORAGE_KEY] = plans
    plans[plan_id] = {
        "actions": actions,
        "user_id": user_id,
        "created": datetime.utcnow().isoformat(),
        "page_uid": getattr(obj, "UID", lambda: None)(),
    }


def _load_plan(obj, plan_id: str) -> Optional[Dict[str, Any]]:
    annotations = IAnnotations(obj)
    plans = annotations.get(PLAN_STORAGE_KEY) or {}
    return plans.get(plan_id)


@implementer(IPublishTraverse)
class AIActionsService(ServiceBase):

    def __init__(self, context, request):
        super().__init__(context, request)
        self.subpath = None

    def publishTraverse(self, request, name):
        if self.subpath is None:
            self.subpath = name
            return self
        raise BadRequest("Too many path segments")

    def reply(self):
        if self.subpath == "plan":
            return self._handle_plan()
        if self.subpath == "apply":
            return self._handle_apply()
        raise BadRequest("Unknown action endpoint")

    def _handle_plan(self):
        data = json_body(self.request) or {}
        if not isinstance(data, dict):
            raise BadRequest("JSON object expected")

        goal = data.get("goal") or ""
        translate_opts = data.get("translation") if isinstance(data.get("translation"), dict) else None
        if not isinstance(goal, str):
            goal = ""
        if not goal.strip() and not translate_opts:
            raise BadRequest("Missing goal")

        target = _resolve_target(self.context, data)
        _ensure_editor(target)

        actions = _derive_actions(translate_opts=translate_opts)
        preview = _preview_from_actions(actions)

        plan_id = str(uuid.uuid4())
        user_id = api.user.get_current().getId()
        _store_plan(target, plan_id, actions, user_id)

        return {
            "plan_id": plan_id,
            "actions": actions,
            "preview": preview,
            "translation_report": _build_translation_stub(actions),
        }

    def _handle_apply(self):
        data = json_body(self.request) or {}
        if not isinstance(data, dict):
            raise BadRequest("JSON object expected")

        target = _resolve_target(self.context, data)
        _ensure_editor(target)

        inline_actions = data.get("actions")
        plan_id = data.get("plan_id")
        actions = inline_actions

        if plan_id:
            plan = _load_plan(target, plan_id)
            if plan:
                plan_actions = plan.get("actions") or []
                if plan_actions:
                    actions = plan_actions
            else:
                logger.warning("Plan %s not found on target, falling back to inline actions", plan_id)

        if not isinstance(actions, list) or not actions:
            raise BadRequest("Missing actions to apply")

        changed = _apply_actions(target, actions)
        log_ai_action(target, actions, plan_id=plan_id)

        report = getattr(target, "_v_last_translation_report", None)
        if hasattr(target, "__delattr__"):
            try:
                delattr(target, "_v_last_translation_report")
            except Exception:
                pass

        return {
            "result": "ok",
            "changed": changed,
            "reload": True,
            "report": report,
        }


_SYNC_TOLERANCE_SECONDS = 5

class AITranslationStatusService(Service):

    def reply(self):
        target = self.context
        if IPloneSiteRoot.providedBy(target):
            return {"translations": [], "outdated_count": 0}

        try:
            from plone.app.multilingual.interfaces import ITranslationManager
        except ImportError:
            return {"translations": [], "outdated_count": 0}

        try:
            manager = ITranslationManager(target)
        except TypeError:
            return {"translations": [], "outdated_count": 0}

        translations = manager.get_translations() or {}
        source_lang = target.Language() or "de"
        source_modified = target.modified()
        source_ts = source_modified.timeTime()

        result = []
        for lang, trans_obj in translations.items():
            if lang == source_lang:
                continue
            try:
                trans_modified = trans_obj.modified()
                trans_ts = trans_modified.timeTime()
                is_outdated = (source_ts - trans_ts) > _SYNC_TOLERANCE_SECONDS
                logger.info(
                    "[KYRA AI STATUS] lang=%s trans_modified=%s diff=%.1fs is_outdated=%s",
                    lang,
                    trans_modified.ISO8601(),
                    source_ts - trans_ts,
                    is_outdated,
                )
                result.append({
                    "language": lang,
                    "title": trans_obj.Title() or "",
                    "url": trans_obj.absolute_url(),
                    "modified": trans_modified.ISO8601(),
                    "is_outdated": is_outdated,
                })
            except Exception:
                continue

        return {
            "source_language": source_lang,
            "source_modified": source_modified.ISO8601(),
            "translations": result,
            "outdated_count": sum(1 for t in result if t["is_outdated"]),
        }
