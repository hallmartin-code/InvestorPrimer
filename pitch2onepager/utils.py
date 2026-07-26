"""Shared helpers: errors, file-type detection, text cleaning."""

from __future__ import annotations

import os
import re
from pathlib import Path

SUPPORTED_EXTENSIONS = {".pdf", ".pptx"}

# Minimum characters of extracted text before a deck is considered analysable.
MIN_DECK_CHARS = 200


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class Pitch2OnePagerError(Exception):
    """Base class for all application errors."""


class FileError(Pitch2OnePagerError):
    """Input file missing, unreadable, or of an unsupported type. Exit code 1."""


class ExtractionError(Pitch2OnePagerError):
    """The deck could not be parsed into usable text. Exit code 2."""


class AnalysisError(Pitch2OnePagerError):
    """The LLM did not return a usable analysis. Exit code 2."""


class APIError(Pitch2OnePagerError):
    """Anthropic API key missing, or the API failed after retries. Exit code 3."""


class BuildError(Pitch2OnePagerError):
    """The output PDF could not be written. Exit code 1."""


# --------------------------------------------------------------------------- #
# File handling
# --------------------------------------------------------------------------- #


def detect_file_type(file_path: str | os.PathLike[str]) -> str:
    """Return ``"pdf"`` or ``"pptx"`` based on the file extension.

    Raises:
        FileError: if the extension is not supported.
    """
    suffix = Path(file_path).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise FileError(
            f"Unsupported file type '{suffix or '(none)'}'. Supported formats: {supported}"
        )
    return suffix.lstrip(".")


def validate_input_file(file_path: str | os.PathLike[str]) -> Path:
    """Check the file exists, is a regular file, and has a supported extension."""
    path = Path(file_path)
    if not path.exists():
        raise FileError(f"File not found: {path}")
    if not path.is_file():
        raise FileError(f"Not a file: {path}")
    detect_file_type(path)
    return path


# --------------------------------------------------------------------------- #
# Text cleaning
# --------------------------------------------------------------------------- #

_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_URL_RE = re.compile(r"\bhttps?://\S+\b")
_PAGE_NUM_RE = re.compile(r"^\s*(?:page\s*)?\d{1,3}\s*(?:/\s*\d{1,3})?\s*$", re.IGNORECASE)
_CONFIDENTIAL_RE = re.compile(
    r"^\s*(confidential|proprietary|private\s*&?\s*confidential|"
    r"do\s+not\s+distribute|all\s+rights\s+reserved)\W*$",
    re.IGNORECASE,
)
_COPYRIGHT_RE = re.compile(r"^\s*(?:©|\(c\)|copyright)\s*\d{4}.*$", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"[ \t ]+")


def _is_boilerplate(line: str, company_hint: str | None = None) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if _PAGE_NUM_RE.match(stripped):
        return True
    if _CONFIDENTIAL_RE.match(stripped):
        return True
    if _COPYRIGHT_RE.match(stripped):
        return True
    # A line that is nothing but an email address or URL carries no narrative value.
    without_contacts = _EMAIL_RE.sub("", stripped)
    without_contacts = _URL_RE.sub("", without_contacts).strip()
    if not without_contacts:
        return True
    if company_hint and stripped.lower() == company_hint.strip().lower():
        return True
    return False


def clean_text(text: str, company_hint: str | None = None) -> str:
    """Normalise whitespace, strip contact details, and drop boilerplate lines."""
    if not text:
        return ""
    lines = []
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = _WHITESPACE_RE.sub(" ", raw_line).strip()
        if _is_boilerplate(line, company_hint):
            continue
        # Email addresses carry no narrative value wherever they appear.
        line = _WHITESPACE_RE.sub(" ", _EMAIL_RE.sub("", line)).strip()
        if not line:
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def guess_title(text: str, max_chars: int = 120) -> str | None:
    """Best-guess slide title: the first non-empty line, if it is short enough."""
    for line in text.split("\n"):
        candidate = line.strip()
        if candidate:
            return candidate[:max_chars]
    return None


def truncate(text: str, max_chars: int) -> str:
    """Trim ``text`` to ``max_chars``, appending an ellipsis when it is cut."""
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)].rstrip() + "…"


def slugify(value: str, fallback: str = "company") -> str:
    """Turn a company name into a filesystem-safe slug."""
    slug = re.sub(r"[^\w\s-]", "", value, flags=re.UNICODE).strip()
    slug = re.sub(r"[\s_-]+", "_", slug)
    return slug or fallback
