from datetime import datetime
import io
import uuid
from typing import Any, Dict, Optional
import mimetypes
import re
import os
import shutil
import subprocess
import tempfile

from interaktiv.kyra import logger
from interaktiv.kyra.services.base import ServiceBase
from plone import api
from plone.protect.interfaces import IDisableCSRFProtection
from zope.publisher.interfaces import IPublishTraverse
from zope.annotation.interfaces import IAnnotations
from zope.interface import alsoProvides
from zExceptions import BadRequest
import json

ANNOTATION_KEY = "interaktiv.kyra.ai_uploads"
MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10 MB


def _normalize_extracted_text(value: str) -> str:
    """Normalize common OCR artifacts and bullet markers."""
    if not isinstance(value, str):
        return ""
    # Keep ß/umlauts intact; only remove obvious artifacts
    replacements = {
        "•": "-",
        "·": "-",
        "●": "-",
        "▪": "-",
        "◦": "-",
        "–": "-",
        "—": "-",
        "‑": "-",
        "©": "",
        "®": "",
        "™": "",
    }
    for src, dst in replacements.items():
        value = value.replace(src, dst)
    # Collapse bullet-like markers to "- "
    value = re.sub(r"[•·●▪◦*]\s*", "- ", value)
    # Remove stray double spaces
    value = re.sub(r"\s{2,}", " ", value)
    return value


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
    normalized = _normalize_extracted_text(value)
    return " ".join(normalized.split())[:limit]


def _clean_text_preserve_newlines(value: str, limit: int = 8000) -> str:
    if not isinstance(value, str):
        return ""
    value = _normalize_extracted_text(value)
    lines = []
    for line in value.splitlines():
        # replace escaped backslashes with space
        line = re.sub(r"\\+", " ", line)
        stripped = " ".join(line.split())
        if stripped:
            lines.append(stripped)
    cleaned = "\n".join(lines)
    return cleaned[:limit]


def _looks_like_rtf_header_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    tokens = stripped.split()
    header_tokens = re.compile(r"^[a-z]{1,6}\d{0,5}$", re.IGNORECASE)
    matches = [
        token for token in tokens if header_tokens.match(token.lower())
    ]
    # if all tokens are header-like and there are at least 2 of them, treat as header
    return len(tokens) > 0 and len(matches) == len(tokens)


def _strip_rtf_header(cleaned: str) -> str:
    lines = cleaned.splitlines()
    output = []
    header_skipped = False
    for line in lines:
        if not header_skipped and _looks_like_rtf_header_line(line):
            continue
        header_skipped = True
        output.append(line)
    if not output:
        return cleaned
    candidate = "\n".join(output).strip()
    if candidate.lower().startswith("rtf1") or candidate.lower().startswith("cocoatext"):
        match = re.search(r"viewkind0", candidate, re.IGNORECASE)
        if match:
            candidate = candidate[match.end():].strip()
    # drop lines that still look like header tokens at start
    human_lines = []
    started = False
    for line in candidate.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not started and _looks_like_rtf_header_line(stripped):
            continue
        started = True
        human_lines.append(stripped)
    if human_lines:
        return "\n".join(human_lines).strip()
    return candidate


def _strip_rtf_style_prefix(value: str) -> str:
    tokens = value.split()

    style_re = re.compile(
        r"^(deftab|pard|li|fi|ri|sa|sb|sl|hyphenfactor|tightenfactor|f|fs|cf|co|b|i|u|expnd|expndtw|kerning|outl|strokewidth|strokec|cocoatextscaling|cocoaplatform|d)[-]?\d*$",
        re.IGNORECASE,
    )

    def _is_style_token(tok: str) -> bool:
        cleaned = tok.strip().lstrip("\\").strip("{};")
        return bool(style_re.match(cleaned))

    filtered = [tok for tok in tokens if not _is_style_token(tok)]
    return " ".join(filtered).strip()


def _clean_rtf_body(raw: str) -> str:
    if not isinstance(raw, str):
        return ""
    cleaned = _clean_text_preserve_newlines(raw)
    stripped = _strip_rtf_header(cleaned)
    result = stripped or cleaned
    result = _strip_rtf_style_prefix(result)
    return result


def _set_tesseract_path():
    """Ensure pytesseract uses a valid binary."""
    try:
        import pytesseract  # type: ignore

        if getattr(pytesseract.pytesseract, "tesseract_cmd", None):
            return
        candidate = os.environ.get("TESSERACT_PATH") or "/opt/homebrew/bin/tesseract"
        if os.path.exists(candidate):
            pytesseract.pytesseract.tesseract_cmd = candidate
    except Exception:
        return


def _extract_text_from_file(data: bytes, content_type: Optional[str], filename: Optional[str] = None) -> str:
    """Best-effort text extraction: plain text, RTF, PDFs, and images (OCR)."""
    ctype = (content_type or "").lower()
    if not ctype and filename:
        guess, _enc = mimetypes.guess_type(filename)
        if guess:
            ctype = guess.lower()

    # RTF extraction
    if filename and filename.lower().endswith(".rtf"):
        # Prefer striprtf to preserve headings and blank lines
        try:
            from striprtf.striprtf import rtf_to_text  # type: ignore

            raw = data.decode("utf-8", errors="ignore")
            text = rtf_to_text(raw)
            cleaned = _clean_rtf_body(text)
            if cleaned:
                return cleaned
        except Exception:
            pass

        # Manual fallback: light cleanup without aggressive header stripping
        try:
            raw = data.decode("utf-8", errors="ignore")
            raw = raw.replace("\\par", "\n").replace("\\line", "\n")
            text = re.sub(r"{\\\*[^}]+}", " ", raw)
            text = re.sub(r"\\'[0-9a-fA-F]{2}", " ", text)
            text = re.sub(r"\\[a-zA-Z]+-?\\d*", " ", text)
            text = re.sub(r"[{}]", " ", text)
            cleaned = _clean_rtf_body(text)
            if cleaned:
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

        # PDF extraction: try pdfminer, PyPDF2, then OCR
    if "pdf" in ctype:
        extracted = ""
        # pdftotext (command line) early fallback if available
        try:
            pdftotext_bin = shutil.which("pdftotext") or "/opt/homebrew/bin/pdftotext"
            if pdftotext_bin and os.path.exists(pdftotext_bin):
                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as tmp_pdf:
                    tmp_pdf.write(data)
                    tmp_pdf.flush()
                    result = subprocess.run(
                        [pdftotext_bin, "-layout", "-l", "1", tmp_pdf.name, "-"],
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    text = result.stdout or result.stderr
                    text = _clean_text(text)
                    if text:
                        logger.info("PDF text extracted via pdftotext (%s chars): %.200s", len(text), text)
                        return text
        except Exception as exc:
            logger.debug("PDF pdftotext extraction failed: %s", exc)
        # pdfminer first
        try:
            from pdfminer.high_level import extract_text  # type: ignore

            text = extract_text(io.BytesIO(data))
            extracted = _clean_text(text)
            if extracted:
                logger.info("PDF text extracted via pdfminer (%s chars): %.200s", len(extracted), extracted)
                return extracted
        except Exception as exc:
            logger.debug("PDF pdfminer extraction failed: %s", exc)

        # PyPDF2 fallback
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
            if extracted:
                logger.info("PDF text extracted via PyPDF2 (%s chars): %.200s", len(extracted), extracted)
                return extracted
        except Exception as exc:
            logger.debug("PDF text extraction failed: %s", exc)

        # OCR fallback
        try:
            _set_tesseract_path()
            import pytesseract  # type: ignore
            from pdf2image import convert_from_bytes  # type: ignore
            ocr_lang = os.environ.get("TESSERACT_LANG", "deu+eng")

            poppler_path = os.environ.get("POPPLER_PATH") or "/opt/homebrew/opt/poppler/bin"
            kwargs = {"first_page": 1, "last_page": 1}
            if poppler_path:
                kwargs["poppler_path"] = poppler_path
            images = convert_from_bytes(data, **kwargs)
            if images:
                text = pytesseract.image_to_string(images[0], lang=ocr_lang)
                extracted = _clean_text(text)
                if extracted:
                    logger.info("PDF OCR extracted (%s chars): %.200s", len(extracted), extracted)
                    return extracted
        except Exception as exc:
            logger.debug("PDF OCR failed: %s", exc)

        # CLI OCR fallback: pdftoppm + tesseract
        try:
            pdftoppm_bin = (
                shutil.which("pdftoppm")
                or "/opt/homebrew/bin/pdftoppm"
                or "/opt/homebrew/opt/poppler/bin/pdftoppm"
            )
            tesseract_bin = os.environ.get("TESSERACT_PATH") or shutil.which("tesseract") or "/opt/homebrew/bin/tesseract"
            ocr_lang = os.environ.get("TESSERACT_LANG", "deu+eng")
            if pdftoppm_bin and os.path.exists(pdftoppm_bin) and tesseract_bin and os.path.exists(tesseract_bin):
                with tempfile.TemporaryDirectory() as tmpdir:
                    pdf_path = os.path.join(tmpdir, "input.pdf")
                    out_prefix = os.path.join(tmpdir, "page")
                    with open(pdf_path, "wb") as fh:
                        fh.write(data)
                    subprocess.run(
                        [pdftoppm_bin, "-f", "1", "-l", "1", "-singlefile", "-png", pdf_path, out_prefix],
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    img_path = f"{out_prefix}.png"
                    if os.path.exists(img_path):
                        # Prefer python OCR if available, else CLI tesseract
                        try:
                            _set_tesseract_path()
                            import pytesseract  # type: ignore
                            from PIL import Image  # type: ignore

                            with open(img_path, "rb") as fh:
                                img_data = fh.read()
                            img = Image.open(io.BytesIO(img_data))
                            text = pytesseract.image_to_string(img, lang=ocr_lang)
                            cleaned = _clean_text(text)
                            if cleaned:
                                logger.info("PDF OCR via pdftoppm + pytesseract (%s chars): %.200s", len(cleaned), cleaned)
                                return cleaned
                        except Exception:
                            pass
                        try:
                            result = subprocess.run(
                                [tesseract_bin, img_path, os.path.join(tmpdir, "out"), "-l", ocr_lang],
                                check=False,
                                capture_output=True,
                                text=True,
                            )
                            out_txt = os.path.join(tmpdir, "out.txt")
                            if result.returncode == 0 and os.path.exists(out_txt):
                                with open(out_txt, "r", encoding="utf-8", errors="ignore") as fh:
                                    text = fh.read()
                                    cleaned = _clean_text(text)
                                    if cleaned:
                                        logger.info(
                                            "PDF OCR via pdftoppm + tesseract CLI (%s chars): %.200s", len(cleaned), cleaned
                                        )
                                        return cleaned
                        except Exception as exc:
                            logger.debug("PDF OCR via CLI failed: %s", exc)
        except Exception as exc:
            logger.debug("PDF CLI OCR failed: %s", exc)

        return "Attachment uploaded (PDF), but no text could be extracted."

    # Image OCR via pytesseract if available
    if ctype.startswith("image/"):
        try:
            _set_tesseract_path()
            import pytesseract  # type: ignore
            from PIL import Image  # type: ignore
            ocr_lang = os.environ.get("TESSERACT_LANG", "deu+eng")

            img = Image.open(io.BytesIO(data))
            text = pytesseract.image_to_string(img, lang=ocr_lang)
            cleaned = _clean_text(text)
            if cleaned:
                logger.info("Image OCR extracted (%s chars): %.200s", len(cleaned), cleaned)
                return cleaned
        except Exception as exc:
            logger.debug("Image OCR failed: %s", exc)
        # fallback to CLI tesseract if available
        try:
            tesseract_bin = os.environ.get("TESSERACT_PATH") or shutil.which("tesseract")
            ocr_lang = os.environ.get("TESSERACT_LANG", "deu+eng")
            if tesseract_bin:
                with tempfile.NamedTemporaryFile(suffix=".img", delete=True) as tmp_img, tempfile.NamedTemporaryFile(
                    suffix=".txt", delete=True
                ) as tmp_txt:
                    tmp_img.write(data)
                    tmp_img.flush()
                    result = subprocess.run(
                        [tesseract_bin, tmp_img.name, tmp_txt.name.replace(".txt", ""), "-l", ocr_lang],
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    if result.returncode == 0 and os.path.exists(tmp_txt.name):
                        with open(tmp_txt.name, "r", encoding="utf-8", errors="ignore") as fh:
                            text = fh.read()
                            cleaned = _clean_text(text)
                            if cleaned:
                                logger.info("Image OCR extracted via CLI (%s chars): %.200s", len(cleaned), cleaned)
                                return cleaned
        except Exception as exc:
            logger.debug("Image OCR CLI failed: %s", exc)
        return "Attachment uploaded (image), but no text could be extracted."

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
        logger.info(
            "[AI CHAT UPLOAD] file=%s content_type=%s size=%s extracted_len=%s preview=%.200s",
            filename,
            content_type,
            len(data),
            len(extracted_text),
            extracted_text or "",
        )
        self.request.response.setHeader("Content-Type", "application/json")
        return json.dumps(payload)
