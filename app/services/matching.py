from __future__ import annotations

import re

from app.db.models import WordFilter

ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"), None)

MATCH_TYPES = {"word", "substring", "regex"}


def normalize_text(text: str) -> str:
    return " ".join(text.translate(ZERO_WIDTH).split())


def validate_pattern(pattern: str, match_type: str) -> str | None:
    """Return an error message for an invalid pattern, otherwise None."""
    if not pattern.strip():
        return "Шаблон не может быть пустым."
    if match_type not in MATCH_TYPES:
        return "Неизвестный тип совпадения."
    if match_type == "regex":
        try:
            re.compile(pattern)
        except re.error as exc:
            return f"Некорректное регулярное выражение: {exc}"
    return None


def _word_pattern(pattern: str) -> str:
    escaped = re.escape(normalize_text(pattern))
    prefix = r"(?<!\w)" if pattern[:1].isalnum() or pattern[:1] == "_" else ""
    suffix = r"(?!\w)" if pattern[-1:].isalnum() or pattern[-1:] == "_" else ""
    return f"{prefix}{escaped}{suffix}"


def matches(
    pattern: str,
    text: str,
    *,
    match_type: str = "word",
    case_sensitive: bool = False,
) -> bool:
    if not text:
        return False

    if match_type == "regex":
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            return re.search(pattern, text.translate(ZERO_WIDTH), flags) is not None
        except re.error:
            return False

    if match_type == "substring":
        prepared_text = text.translate(ZERO_WIDTH)
        if case_sensitive:
            return pattern in prepared_text
        return pattern.casefold() in prepared_text.casefold()

    prepared_text = normalize_text(text)
    prepared_pattern = _word_pattern(pattern)
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.search(prepared_pattern, prepared_text, flags) is not None


def find_matching_filter(
    filters: list[WordFilter],
    text: str,
    caption: str | None,
) -> WordFilter | None:
    for item in filters:
        if matches(
            item.pattern,
            text,
            match_type=item.match_type,
            case_sensitive=item.case_sensitive,
        ) or (
            caption
            and matches(
                item.pattern,
                caption,
                match_type=item.match_type,
                case_sensitive=item.case_sensitive,
            )
        ):
            return item
    return None
