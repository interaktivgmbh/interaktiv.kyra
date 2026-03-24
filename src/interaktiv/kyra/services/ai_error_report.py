import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

import requests
from plone import api
from plone.protect.interfaces import IDisableCSRFProtection
from plone.restapi.services import Service
from zExceptions import BadRequest
from zope.interface import alsoProvides

from interaktiv.kyra.registry.ai_assistant import IAIAssistantSchema

logger = logging.getLogger(__name__)


def _get_github_config() -> Dict[str, str]:
    try:
        token = api.portal.get_registry_record(
            name="github_token", interface=IAIAssistantSchema
        ) or ""
        repo = api.portal.get_registry_record(
            name="github_repo", interface=IAIAssistantSchema
        ) or ""
        return {"token": token, "repo": repo}
    except Exception:
        return {"token": "", "repo": ""}


def _create_github_issue(
    token: str,
    repo: str,
    title: str,
    body: str,
    labels: list = None,
) -> Optional[Dict[str, Any]]:
    """Create a GitHub issue via the REST API."""
    if not token or not repo:
        logger.warning("[KYRA ERROR REPORT] GitHub token or repo not configured")
        return None

    url = f"https://api.github.com/repos/{repo}/issues"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    payload = {
        "title": title,
        "body": body,
        "labels": labels or ["auto-reported", "bug"],
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        response.raise_for_status()
        data = response.json()
        logger.info(
            "[KYRA ERROR REPORT] Created issue #%s: %s",
            data.get("number"),
            data.get("html_url"),
        )
        return {
            "issue_number": data.get("number"),
            "issue_url": data.get("html_url"),
        }
    except Exception as exc:
        logger.error("[KYRA ERROR REPORT] Failed to create issue: %s", exc)
        return None


def _format_issue_body(data: Dict[str, Any]) -> str:
    """Format error data into a GitHub issue body."""
    error_message = data.get("error_message", "Unknown error")
    error_type = data.get("error_type", "")
    stack_trace = data.get("stack_trace", "")
    page_url = data.get("page_url", "")
    user_action = data.get("user_action", "")
    browser = data.get("browser", "")
    timestamp = data.get("timestamp", datetime.utcnow().isoformat())
    component = data.get("component", "")

    lines = [
        "## Auto-Reported Error",
        "",
        f"**Error:** {error_message}",
    ]

    if error_type:
        lines.append(f"**Type:** `{error_type}`")
    if component:
        lines.append(f"**Component:** `{component}`")
    if page_url:
        lines.append(f"**Page:** {page_url}")
    if user_action:
        lines.append(f"**User Action:** {user_action}")
    if browser:
        lines.append(f"**Browser:** {browser}")

    lines.append(f"**Timestamp:** {timestamp}")

    if stack_trace:
        lines.extend([
            "",
            "## Stack Trace",
            "```",
            stack_trace[:3000],
            "```",
        ])

    lines.extend([
        "",
        "---",
        "*This issue was automatically created by Kyra AI Error Reporter.*",
    ])

    return "\n".join(lines)


class AIErrorReport(Service):
    """POST /@ai-error-report — auto-create GitHub issue from error."""

    def __init__(self, context, request):
        super().__init__(context, request)
        alsoProvides(self.request, IDisableCSRFProtection)

    def reply(self):
        raw = self.request.get("BODY", b"")
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        try:
            data = json.loads(raw) if raw else {}
        except (json.JSONDecodeError, ValueError):
            data = {}

        if not data:
            try:
                from plone.restapi.deserializer import json_body
                data = json_body(self.request) or {}
            except Exception:
                pass

        error_message = data.get("error_message", "")
        if not error_message:
            raise BadRequest("Missing 'error_message'")

        config = _get_github_config()
        if not config["token"] or not config["repo"]:
            return {
                "status": "skipped",
                "reason": "GitHub token or repo not configured",
            }

        # Build issue title (truncated)
        error_type = data.get("error_type", "Error")
        title_text = error_message[:80]
        if len(error_message) > 80:
            title_text += "..."
        title = f"[Auto] {error_type}: {title_text}"

        body = _format_issue_body(data)

        result = _create_github_issue(
            config["token"],
            config["repo"],
            title,
            body,
        )

        if result:
            return {
                "status": "created",
                "issue_number": result["issue_number"],
                "issue_url": result["issue_url"],
            }

        return {"status": "failed", "reason": "Could not create GitHub issue"}
