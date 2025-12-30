from datetime import datetime
import io
import uuid
from typing import Any, Dict, Optional
import mimetypes
import re

from interaktiv.kyra.services.base import ServiceBase
from plone import api
from plone.protect.interfaces import IDisableCSRFProtection
from zope.publisher.interfaces import IPublishTraverse
from zope.annotation.interfaces import IAnnotations
from zope.interface import alsoProvides
from zExceptions import BadRequest
import json
import logging

logger = logging.getLogger(__name__)


ANNOTATION_KEY = "interaktiv.kyra.ai_uploads"
MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10 MB


def _get_uploads_store() -> Dict[str, Any]:
    portal = api.portal.get()
    annotations = IAnnotations(portal)
    store = annotations.get(ANNOTATION_KEY)
    if store is None or not isinstance(store, dict):
        store = {}
        annotations[ANNOTATION_KEY] = store
    return store


def _clean_text(value: str, limit: int = 8000) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def _clean_text_preserve_newlines(value: str, limit: int = 8000) -> str:
    if not isinstance(value, str):
        return ""
    lines = []
    for line in value.splitlines():
        # replace escaped backslashes with space
        line = re.sub(r"\\+", " ", line)
        stripped = " ".join(line.split())
        if stripped:
            lines.append(stripped)
    cleaned = "\n".join(lines)
    return cleaned[:limit]


def _strip_rtf_header(cleaned: str) -> str:
    stop_tokens = (
        "rtf",
        "cocoartf",
        "fonttbl",
        "colortbl",
        "vieww",
        "viewh",
        "viewkind",
        "pard",
        "fcharset",
        "ansicpg",
        "paperw",
        "paperh",
        "tx720",
        "tx1440",
        "tx2160",
        "tx2880",
        "tx3600",
        "tx4320",
        "tx5040",
        "tx5760",
        "tx6480",
        "tx7200",
        "tx7920",
        "tx8640",
        "dirnatural",
        "tightenfactor",
    )
    lines = cleaned.splitlines()
    kept = []
    for line in lines:
        lower = line.lower()
        if any(token in lower for token in stop_tokens):
            continue
        kept.append(line)
    # if we stripped everything, return original
    if not kept:
        kept = [cleaned]
    merged = "\n".join(kept)
    # remove lingering style tokens like f0 fs24 cf0
    merged = re.sub(r"\b[a-z]{1,3}\d{1,4}\b", " ", merged, flags=re.IGNORECASE)
    # drop tokens that are just style markers (f0, fs24, cf0, etc.)
    tokens = []
    for token in merged.split():
        low = token.lower()
        if re.match(r"^[a-z]{1,3}\d{1,4}$", low):
            continue
        tokens.append(token)
    merged = " ".join(tokens)
    return merged.strip()


def _extract_text_from_file(data: bytes, content_type: Optional[str], filename: Optional[str] = None) -> str:
    """Best-effort text extraction: plain text and PDFs; images are skipped unless OCR is available."""
    ctype = (content_type or "").lower()
    if not ctype and filename:
        guess, _enc = mimetypes.guess_type(filename)
        if guess:
            ctype = guess.lower()

    # RTF extraction
    if filename and filename.lower().endswith(".rtf"):
        # Try striprtf first
        try:
            from striprtf.striprtf import rtf_to_text  # type: ignore

            raw = data.decode("utf-8", errors="ignore")
            text = rtf_to_text(raw)
            return _clean_text_preserve_newlines(text)
        except Exception:
            pass
        try:
            raw = data.decode("utf-8", errors="ignore")
            # convert paragraph/line markers to newlines early
            raw = raw.replace("\\par", "\n").replace("\\line", "\n")
            # remove groups like {\*\...}
            text = re.sub(r"{\\\*[^}]+}", " ", raw)
            # remove control words and hex escapes
            text = re.sub(r"\\'[0-9a-fA-F]{2}", " ", text)
            text = re.sub(r"\\[a-zA-Z]+-?\\d*", " ", text)
            # strip remaining braces
            text = re.sub(r"[{}]", " ", text)
            cleaned = _clean_text_preserve_newlines(text)
            cleaned = _strip_rtf_header(cleaned)
            return cleaned
        except Exception as exc:
            logger.debug("RTF extraction failed: %s", exc)
            return ""

    # Plain text
    if ctype.startswith("text/"):
        try:
            return _clean_text(data.decode("utf-8", errors="ignore"))
        except Exception:
            return ""

    # PDF extraction via PyPDF2 if available
    if "pdf" in ctype:
        extracted = ""
        try:
            import PyPDF2  # type: ignore

            reader = PyPDF2.PdfReader(io.BytesIO(data))
            pages = []
            for page in reader.pages:
                try:
                    text = page.extract_text() or ""
                    if text:
                        pages.append(text)
                except Exception:
                    continue
            extracted = _clean_text(" ".join(pages))
        except Exception as exc:
            logger.debug("PDF text extraction failed: %s", exc)
            extracted = ""

        if extracted:
            return extracted

        # Fallback OCR on first page if poppler/pdf2image+pytesseract are available
        try:
            import pytesseract  # type: ignore
            from pdf2image import convert_from_bytes  # type: ignore

            images = convert_from_bytes(data, first_page=1, last_page=1)
            if images:
                text = pytesseract.image_to_string(images[0])
                return _clean_text(text)
        except Exception as exc:
            logger.debug("PDF OCR failed: %s", exc)
            return ""

    # Image OCR via pytesseract if available
    if ctype.startswith("image/"):
        try:
            import pytesseract  # type: ignore
            from PIL import Image  # type: ignore

            img = Image.open(io.BytesIO(data))
            text = pytesseract.image_to_string(img)
            return _clean_text(text)
        except Exception as exc:
            logger.debug("Image OCR failed: %s", exc)
            return ""

    return ""


class AIChatUpload(ServiceBase):
    """POST /++api++/@ai-chat/upload

    Accept a file upload and store extracted text for later chat context usage.
    """

    def __call__(self):
        alsoProvides(self.request, IDisableCSRFProtection)
        file_field = self.request.form.get("file")
        if file_field is None:
            raise BadRequest("Missing file")

        filename = getattr(file_field, "filename", None) or "upload"
        data = file_field.read()
        if not data:
            raise BadRequest("Empty file")

        if len(data) > MAX_UPLOAD_SIZE:
            raise BadRequest("File too large")

        headers = getattr(file_field, "headers", {}) if hasattr(file_field, "headers") else {}
        content_type = headers.get("Content-Type") if headers else None

        extracted_text = _extract_text_from_file(data, content_type, filename)

        upload_id = str(uuid.uuid4())
        store = _get_uploads_store()
        store[upload_id] = {
            "filename": filename,
            "size": len(data),
            "content_type": content_type,
            "extracted_text": extracted_text,
            "created": datetime.utcnow().isoformat(),
            "user": api.user.get_current().getId(),
        }

        payload = {
            "file_id": upload_id,
            "name": filename,
            "has_text": bool(extracted_text),
            "text": extracted_text,
        }
        self.request.response.setHeader("Content-Type", "application/json")
        return json.dumps(payload)
