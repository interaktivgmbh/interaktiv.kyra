import random
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from interaktiv.kyra import logger
from interaktiv.kyra.registry.ai_assistant import IAIAssistantSchema
from plone import api
from plone.protect.interfaces import IDisableCSRFProtection
from plone.restapi.deserializer import json_body
from plone.restapi.services import Service
from zExceptions import BadRequest
from zope.annotation.interfaces import IAnnotations
from zope.interface import alsoProvides

try:
    import deepl
except ImportError:
    deepl = None  # type: ignore[assignment]

GLOSSARY_ENTRIES_KEY = "interaktiv.kyra.glossary_entries"
GLOSSARY_IDS_KEY = "interaktiv.kyra.glossary_ids"
GLOSSARY_NAME = "FZJ Kyra Translation Glossary"


# ---------------------------------------------------------------------------
# Registry / annotation helpers
# ---------------------------------------------------------------------------

def _get_deepl_api_key() -> str:
    try:
        return api.portal.get_registry_record(
            name="deepl_api_key", interface=IAIAssistantSchema
        ) or ""
    except Exception:
        return ""


def _get_glossary_ids() -> Dict[str, str]:
    """Return dict of pair_key -> glossary_id (auto-managed, stored in annotations)."""
    try:
        portal = api.portal.get()
        annotations = IAnnotations(portal)
        ids = annotations.get(GLOSSARY_IDS_KEY)
        return dict(ids) if isinstance(ids, dict) else {}
    except Exception:
        return {}


def _get_glossary_id_for_pair(source_lang: str, target_lang: str) -> str:
    """Return the DeepL glossary ID for a specific language pair."""
    ids = _get_glossary_ids()
    return ids.get(_pair_key(source_lang, target_lang), "")


def _set_glossary_ids(ids: Dict[str, str]) -> None:
    try:
        portal = api.portal.get()
        annotations = IAnnotations(portal)
        annotations[GLOSSARY_IDS_KEY] = dict(ids)
    except Exception as exc:
        logger.warning("[KYRA DEEPL] could not save glossary_ids: %s", exc)


# ---------------------------------------------------------------------------
# DeepL client
# ---------------------------------------------------------------------------

def _get_deepl_client():
    """Return a DeepL client or None if not available."""
    if deepl is None:
        logger.warning("[KYRA DEEPL] deepl module not available (not installed?)")
        return None
    key = _get_deepl_api_key()
    if not key.strip():
        logger.warning("[KYRA DEEPL] no API key configured in registry")
        return None
    try:
        return deepl.DeepLClient(key)
    except Exception as exc:
        logger.warning("[KYRA DEEPL] could not create client: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Translate via DeepL
# ---------------------------------------------------------------------------

_LANG_MAP = {
    "en": "EN-US",
    "de": "DE",
    "fr": "FR",
    "es": "ES",
    "it": "IT",
    "nl": "NL",
    "pl": "PL",
    "pt": "PT-PT",
    "ja": "JA",
    "zh": "ZH-HANS",
}

_SOURCE_LANG_MAP = {
    "en": "EN",
    "de": "DE",
    "fr": "FR",
    "es": "ES",
    "it": "IT",
    "nl": "NL",
    "pl": "PL",
    "pt": "PT",
    "ja": "JA",
    "zh": "ZH",
}


def _deepl_target_lang(lang: str) -> str:
    return _LANG_MAP.get(lang.lower().strip(), lang.upper())


def _deepl_source_lang(lang: str) -> str:
    return _SOURCE_LANG_MAP.get(lang.lower().strip(), lang.upper())


# Reverse mapping: DeepL code -> our internal code (e.g. "EN-US" -> "en")
_REVERSE_LANG_MAP: Dict[str, str] = {}
for _k, _v in _LANG_MAP.items():
    _REVERSE_LANG_MAP[_v.upper()] = _k
for _k, _v in _SOURCE_LANG_MAP.items():
    _REVERSE_LANG_MAP[_v.upper()] = _k


def _internal_lang(deepl_code: str) -> str:
    """Convert DeepL language code to our internal code (e.g. 'EN-US' -> 'en')."""
    code = deepl_code.upper().strip()
    if code in _REVERSE_LANG_MAP:
        return _REVERSE_LANG_MAP[code]
    # Fallback: try base code (e.g. "EN-US" -> "EN")
    base = code.split("-")[0]
    if base in _REVERSE_LANG_MAP:
        return _REVERSE_LANG_MAP[base]
    return code.lower()


def deepl_translate_text(
    text: str,
    source_lang: str,
    target_lang: str,
    glossary_id: Optional[str] = None,
) -> Optional[str]:
    """Translate text via DeepL. Returns None if DeepL is not available."""
    if not isinstance(text, str) or not text.strip():
        return text or ""
    client = _get_deepl_client()
    if client is None:
        logger.info("[KYRA DEEPL] skipping translation, no client available")
        return None
    kwargs: Dict[str, Any] = {
        "text": text,
        "target_lang": _deepl_target_lang(target_lang),
    }
    if source_lang:
        kwargs["source_lang"] = _deepl_source_lang(source_lang)
    gid = glossary_id or (
        _get_glossary_id_for_pair(source_lang, target_lang) if source_lang else ""
    )
    if gid and source_lang:
        kwargs["glossary"] = gid

    for attempt in range(4):
        try:
            result = client.translate_text(**kwargs)
            translated = str(result)
            if translated.strip():
                logger.info(
                    "[KYRA DEEPL] translated %d chars -> %d chars (%s->%s)",
                    len(text), len(translated), source_lang, target_lang,
                )
                return translated.strip()
            return None
        except Exception as exc:
            exc_str = str(exc)
            if ("Too many requests" in exc_str or "429" in exc_str) and attempt < 3:
                delay = 1.5 * (attempt + 1) + random.uniform(0, 0.5)
                logger.info(
                    "[KYRA DEEPL] rate limited, retrying in %.1fs (attempt %d/3)",
                    delay, attempt + 1,
                )
                time.sleep(delay)
                continue
            logger.warning("[KYRA DEEPL] translation failed, falling back: %s", exc)
            return None
    return None


# ---------------------------------------------------------------------------
# Local glossary entry storage (portal annotations)
# ---------------------------------------------------------------------------

def _get_glossary_store() -> Dict[str, Dict[str, Dict[str, str]]]:
    """
    Returns the local glossary store from portal annotations.
    Structure: { "de:en": { "Forschung": "Research", ... }, ... }
    """
    portal = api.portal.get()
    annotations = IAnnotations(portal)
    store = annotations.get(GLOSSARY_ENTRIES_KEY)
    if not isinstance(store, dict):
        store = {}
        annotations[GLOSSARY_ENTRIES_KEY] = store
    return store


def _pair_key(source_lang: str, target_lang: str) -> str:
    return f"{source_lang.lower().strip()}:{target_lang.lower().strip()}"


def get_glossary_entries(source_lang: str, target_lang: str) -> Dict[str, str]:
    store = _get_glossary_store()
    return dict(store.get(_pair_key(source_lang, target_lang), {}))


def add_glossary_entry(
    source_term: str, target_term: str, source_lang: str, target_lang: str
) -> Dict[str, str]:
    store = _get_glossary_store()
    key = _pair_key(source_lang, target_lang)
    if key not in store:
        store[key] = {}
    store[key][source_term.strip()] = target_term.strip()
    # Persist
    portal = api.portal.get()
    annotations = IAnnotations(portal)
    annotations[GLOSSARY_ENTRIES_KEY] = store
    return dict(store[key])


def remove_glossary_entry(
    source_term: str, source_lang: str, target_lang: str
) -> Dict[str, str]:
    store = _get_glossary_store()
    key = _pair_key(source_lang, target_lang)
    entries = store.get(key, {})
    entries.pop(source_term.strip(), None)
    if not entries:
        store.pop(key, None)
    else:
        store[key] = entries
    portal = api.portal.get()
    annotations = IAnnotations(portal)
    annotations[GLOSSARY_ENTRIES_KEY] = store
    return dict(entries)


# ---------------------------------------------------------------------------
# Sync local entries to DeepL glossary
# ---------------------------------------------------------------------------

def _pull_entries_from_deepl(client) -> int:
    """
    Read all glossaries from DeepL and synchronize them into our local store.
    Remote entries are treated as the source of truth: new remote entries are
    added locally, and local entries that no longer exist in DeepL are removed.
    Returns the number of entries changed (added + removed).
    """
    changed = 0
    try:
        glossaries = client.list_glossaries()
        logger.info("[KYRA DEEPL] pulling entries from %d DeepL glossaries", len(glossaries))

        # Collect all remote entries per language pair
        remote_by_pair: Dict[str, Dict[str, str]] = {}
        for g in glossaries:
            try:
                src = _internal_lang(g.source_lang)
                tgt = _internal_lang(g.target_lang)
                remote_entries = client.get_glossary_entries(g.glossary_id)
                if not remote_entries:
                    continue
                key = _pair_key(src, tgt)
                if key not in remote_by_pair:
                    remote_by_pair[key] = {}
                for term, translation in remote_entries.items():
                    if term.strip() and translation.strip():
                        remote_by_pair[key][term.strip()] = translation.strip()
                logger.info(
                    "[KYRA DEEPL] pulled %d entries from glossary %s (%s->%s, name=%s)",
                    len(remote_entries), g.glossary_id, src, tgt, g.name,
                )
            except Exception as exc:
                logger.warning(
                    "[KYRA DEEPL] could not read glossary %s: %s", g.glossary_id, exc
                )

        # Sync local store with remote: add missing, remove stale
        store = _get_glossary_store()
        for key, remote_entries in remote_by_pair.items():
            if key not in store:
                store[key] = {}
            # Add new remote entries
            for term, translation in remote_entries.items():
                if term not in store[key]:
                    store[key][term] = translation
                    changed += 1
            # Remove local entries that no longer exist in DeepL
            stale = [t for t in store[key] if t not in remote_entries]
            for term in stale:
                del store[key][term]
                changed += 1

        # Also clean up local pairs that have no remote glossary at all
        for key in list(store.keys()):
            if key not in remote_by_pair:
                if store[key]:
                    changed += len(store[key])
                    store[key] = {}

        # Persist
        portal = api.portal.get()
        annotations = IAnnotations(portal)
        annotations[GLOSSARY_ENTRIES_KEY] = store

    except Exception as exc:
        logger.warning("[KYRA DEEPL] could not list glossaries for pull: %s", exc)
    if changed:
        logger.info("[KYRA DEEPL] synced %d entry changes from DeepL", changed)
    return changed


def _cleanup_old_glossaries(client) -> None:
    """Delete ALL glossaries on the account to free quota for new ones."""
    # Clean up ALL bilingual glossaries (not just ours — free tier has strict limits)
    try:
        glossaries = client.list_glossaries()
        logger.info("[KYRA DEEPL] found %d bilingual glossaries to clean up", len(glossaries))
        for g in glossaries:
            try:
                client.delete_glossary(g.glossary_id)
                logger.info("[KYRA DEEPL] deleted glossary %s (%s)", g.glossary_id, g.name)
            except Exception as exc:
                logger.warning("[KYRA DEEPL] could not delete glossary %s: %s", g.glossary_id, exc)
    except Exception as exc:
        logger.warning("[KYRA DEEPL] could not list glossaries: %s", exc)

    # Also clean up any multilingual glossaries (from previous attempts)
    try:
        glossaries = client.list_multilingual_glossaries()
        for g in glossaries:
            try:
                client.delete_multilingual_glossary(g.glossary_id)
                logger.info("[KYRA DEEPL] deleted multilingual glossary %s (%s)", g.glossary_id, g.name)
            except Exception as exc:
                logger.warning("[KYRA DEEPL] could not delete multilingual glossary %s: %s", g.glossary_id, exc)
    except Exception:
        pass  # multilingual API may not be available on free tier


def sync_glossary_to_deepl() -> Optional[str]:
    """
    Sync all local glossary entries to DeepL as bilingual glossaries (v2 API).
    Creates one glossary per language pair. Returns first glossary_id or None.
    """
    client = _get_deepl_client()
    if client is None:
        logger.warning("[KYRA DEEPL] cannot sync glossary: no client")
        return None

    # Log usage for diagnostics
    try:
        usage = client.get_usage()
        logger.info(
            "[KYRA DEEPL] API usage: %s/%s chars (key: %s...)",
            usage.character.count if usage.character else "?",
            usage.character.limit if usage.character else "?",
            _get_deepl_api_key()[:8],
        )
    except Exception as exc:
        logger.warning("[KYRA DEEPL] could not check usage: %s", exc)

    store = _get_glossary_store()
    if not store:
        logger.info("[KYRA DEEPL] no glossary entries to sync")
        return None

    # Delete ALL old glossaries first
    _cleanup_old_glossaries(client)
    _set_glossary_ids({})

    # Create one bilingual glossary per language pair (v2 API)
    new_ids: Dict[str, str] = {}
    first_id = None

    for pair_key, entries in store.items():
        if not entries:
            continue
        parts = pair_key.split(":")
        if len(parts) != 2:
            continue
        src, tgt = parts
        # Use non-regional codes for glossary creation
        src_code = _deepl_source_lang(src)
        tgt_code = _deepl_source_lang(tgt)  # non-regional for glossary

        try:
            glossary = client.create_glossary(
                GLOSSARY_NAME,
                source_lang=src_code,
                target_lang=tgt_code,
                entries=entries,
            )
            gid = glossary.glossary_id
            new_ids[pair_key] = gid
            if first_id is None:
                first_id = gid
            logger.info(
                "[KYRA DEEPL] created glossary %s for %s->%s (%d entries)",
                gid, src_code, tgt_code, len(entries),
            )
        except Exception as exc:
            logger.warning(
                "[KYRA DEEPL] glossary creation failed for %s->%s: %s",
                src_code, tgt_code, exc,
            )

    _set_glossary_ids(new_ids)
    logger.info("[KYRA DEEPL] synced %d glossaries: %s", len(new_ids), new_ids)
    return first_id


# ---------------------------------------------------------------------------
# REST API Service
# ---------------------------------------------------------------------------

def import_glossary_from_csv(
    csv_data: str, source_lang: str, target_lang: str
) -> Tuple[Dict[str, str], Optional[str]]:
    """
    Parse CSV (DeepL format: source,target per line) and import into local
    store + sync to DeepL. Returns (entries, glossary_id).
    """
    import csv
    import io

    reader = csv.reader(io.StringIO(csv_data))
    imported = 0
    for row in reader:
        if len(row) < 2:
            continue
        src = row[0].strip()
        tgt = row[1].strip()
        if not src or not tgt:
            continue
        add_glossary_entry(src, tgt, source_lang, target_lang)
        imported += 1

    logger.info("[KYRA DEEPL] imported %d entries from CSV (%s->%s)", imported, source_lang, target_lang)
    glossary_id = sync_glossary_to_deepl()
    entries = get_glossary_entries(source_lang, target_lang)
    return entries, glossary_id


class AIGlossaryService(Service):

    def __init__(self, context, request):
        super().__init__(context, request)
        alsoProvides(self.request, IDisableCSRFProtection)

    def reply(self):
        method = self.request.method.upper()
        if method == "GET":
            return self._handle_get()
        if method == "POST":
            return self._handle_post()
        if method == "DELETE":
            return self._handle_delete()
        raise BadRequest("Unsupported method")

    def _handle_get(self):
        source_lang = self.request.get("source", "de")
        target_lang = self.request.get("target", "en")

        # Include DeepL diagnostics + pull entries from DeepL
        deepl_status = {"available": False}
        client = _get_deepl_client()
        if client:
            deepl_status["available"] = True
            # Pull entries from DeepL into local store
            try:
                pulled = _pull_entries_from_deepl(client)
                deepl_status["pulled_entries"] = pulled
            except Exception:
                pass
            try:
                usage = client.get_usage()
                if usage.character:
                    deepl_status["characters_used"] = usage.character.count
                    deepl_status["characters_limit"] = usage.character.limit
            except Exception:
                pass
            try:
                deepl_status["glossary_count"] = len(client.list_glossaries())
            except Exception:
                deepl_status["glossary_count"] = 0

        # Read entries AFTER pull so they include DeepL data
        entries = get_glossary_entries(source_lang, target_lang)

        return {
            "source_lang": source_lang,
            "target_lang": target_lang,
            "entries": entries,
            "glossary_id": _get_glossary_id_for_pair(source_lang, target_lang),
            "glossary_ids": _get_glossary_ids(),
            "deepl": deepl_status,
        }

    def _handle_post(self):
        data = json_body(self.request) or {}

        # CSV import mode
        csv_data = data.get("csv_data")
        if isinstance(csv_data, str) and csv_data.strip():
            source_lang = data.get("source_lang", "de")
            target_lang = data.get("target_lang", "en")
            entries, glossary_id = import_glossary_from_csv(csv_data, source_lang, target_lang)
            return {
                "result": "ok",
                "entries": entries,
                "glossary_id": _get_glossary_id_for_pair(source_lang, target_lang),
                "glossary_ids": _get_glossary_ids(),
                "imported": len(entries),
            }

        # Single entry mode
        source_term = data.get("source_term")
        target_term = data.get("target_term")
        source_lang = data.get("source_lang", "de")
        target_lang = data.get("target_lang", "en")

        if not isinstance(source_term, str) or not source_term.strip():
            raise BadRequest("Missing 'source_term'")
        if not isinstance(target_term, str) or not target_term.strip():
            raise BadRequest("Missing 'target_term'")

        entries = add_glossary_entry(source_term, target_term, source_lang, target_lang)
        sync_glossary_to_deepl()

        return {
            "result": "ok",
            "entries": entries,
            "glossary_id": _get_glossary_id_for_pair(source_lang, target_lang),
        }

    def _handle_delete(self):
        data = json_body(self.request) or {}
        source_term = data.get("source_term")
        source_lang = data.get("source_lang", "de")
        target_lang = data.get("target_lang", "en")

        if not isinstance(source_term, str) or not source_term.strip():
            raise BadRequest("Missing 'source_term'")

        entries = remove_glossary_entry(source_term, source_lang, target_lang)
        sync_glossary_to_deepl()

        return {
            "result": "ok",
            "entries": entries,
            "glossary_id": _get_glossary_id_for_pair(source_lang, target_lang),
        }
