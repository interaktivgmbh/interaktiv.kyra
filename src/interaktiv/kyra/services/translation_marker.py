"""Persistent marker for machine-translated (KI-erzeugt) content.

Records, on the target object of an automated translation, that its content was
produced by the machine-translation pipeline and has not yet been edited by a
human. Whether the content has since been reviewed is derived from the object's
``modified`` timestamp: the marker stores the ``modified`` value captured right
after the translation was applied, so any later editorial change bumps
``modified`` past that value and the content counts as reviewed.

This reuses the same timestamp-comparison approach already used for the
"outdated translation" detection and deliberately avoids an
``IObjectModifiedEvent`` subscriber, which would otherwise be triggered by the
translation pipeline's own writes.
"""

from datetime import datetime
from typing import Any, Dict, Optional

from interaktiv.kyra import logger
from zope.annotation.interfaces import IAnnotations

MARKER_KEY = "interaktiv.kyra.ai_translated"

# Tolerance (seconds) for the modified-timestamp comparison, matching the
# tolerance used for outdated-translation detection.
REVIEW_TOLERANCE_SECONDS = 5.0


def _modified_ts(obj) -> Optional[float]:
    try:
        return obj.modified().timeTime()
    except Exception:
        return None


def mark_ai_translated(obj, source_language: str, gateway_used: bool) -> None:
    """Mark ``obj`` as machine-translated and not yet editorially reviewed.

    Call this as the very last step after all translation writes to the object,
    so the stored ``modified`` timestamp reflects the fully translated state.
    """
    try:
        annotations = IAnnotations(obj)
        annotations[MARKER_KEY] = {
            "translated_at": datetime.utcnow().isoformat(),
            "source_language": source_language,
            "gateway_used": bool(gateway_used),
            # modified timestamp right after translation; used to detect edits
            "modified_at": _modified_ts(obj),
        }
    except Exception as exc:
        logger.warning("[KYRA AI MARKER] could not mark %r: %s", obj, exc)


def clear_ai_translated(obj) -> None:
    """Remove the machine-translation marker from ``obj``."""
    try:
        annotations = IAnnotations(obj)
        if MARKER_KEY in annotations:
            del annotations[MARKER_KEY]
    except Exception as exc:
        logger.warning("[KYRA AI MARKER] could not clear marker on %r: %s", obj, exc)


def get_marker(obj) -> Optional[Dict[str, Any]]:
    """Return the raw marker annotation, or ``None`` if not machine-translated."""
    try:
        annotations = IAnnotations(obj)
        marker = annotations.get(MARKER_KEY)
        return dict(marker) if marker else None
    except Exception:
        return None


def is_machine_translated(obj) -> bool:
    """Whether ``obj`` was produced by the automated translation pipeline."""
    return get_marker(obj) is not None


def is_unreviewed(obj) -> bool:
    """Whether ``obj`` is machine-translated AND has not been edited since.

    Returns ``False`` for content that was never machine-translated or that has
    been editorially changed after translation.
    """
    marker = get_marker(obj)
    if not marker:
        return False
    stored = marker.get("modified_at")
    current = _modified_ts(obj)
    if stored is None or current is None:
        # Cannot decide from timestamps — treat as still unreviewed so the
        # KI-marker stays visible rather than silently disappearing.
        return True
    return (current - stored) <= REVIEW_TOLERANCE_SECONDS


def marker_info(obj) -> Dict[str, Any]:
    """Compact status dict for API responses."""
    marker = get_marker(obj)
    if not marker:
        return {"is_machine_translated": False, "is_unreviewed": False}
    return {
        "is_machine_translated": True,
        "is_unreviewed": is_unreviewed(obj),
        "translated_at": marker.get("translated_at"),
        "source_language": marker.get("source_language"),
        "gateway_used": marker.get("gateway_used"),
    }
