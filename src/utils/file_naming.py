"""Build portable, descriptive filenames without changing download identity."""

import os
import re
import unicodedata
from datetime import UTC, timedelta, timezone

BEIJING = timezone(timedelta(hours=8))
MAX_FILENAME_BYTES = 240


def _clean(value: str) -> str:
    value = unicodedata.normalize("NFC", value)
    value = "".join(
        " " if unicodedata.category(char).startswith("C") else char for char in value
    )
    value = re.sub(r'[<>:"/\\|?*]', "_", value)
    return re.sub(r"\s+", " ", value).strip(" ._")


def _shorten(value: str, budget: int) -> str:
    return value.encode("utf-8")[:budget].decode("utf-8", errors="ignore").rstrip(" ._")


def _source_name(origin) -> str:
    if origin is None:
        return "来源未知"
    for attribute in ("chat", "sender_chat", "sender_user"):
        sender = getattr(origin, attribute, None)
        if sender is not None:
            for field in ("title", "full_name", "username", "first_name"):
                name = getattr(sender, field, None)
                if name:
                    return str(name)
    return getattr(origin, "sender_user_name", None) or "来源未知"


def build_download_name(message, telegram_file, original_file_name: str) -> str:
    """Return original_source_YYYY-MM-DD_caption_full-unique-id.extension.

    Dates use the original forward date when available, otherwise the received
    message date. All dates are converted to Beijing time. Only readable fields
    are shortened; the complete Telegram unique ID and extension are preserved.
    """
    unique_id = telegram_file.file_unique_id
    if not unique_id or not re.fullmatch(r"[A-Za-z0-9_-]+", unique_id):
        raise ValueError("Invalid Telegram file_unique_id")

    safe_original = _clean(original_file_name)
    stem, extension = os.path.splitext(safe_original)
    stem = stem or "file"
    origin = getattr(message, "forward_origin", None)
    sent_at = getattr(origin, "date", None) or message.date
    if sent_at.tzinfo is None:
        sent_at = sent_at.replace(tzinfo=UTC)
    date = sent_at.astimezone(BEIJING).strftime("%Y-%m-%d")

    source = _clean(_source_name(origin)) or "来源未知"
    caption = _clean(getattr(message, "caption", None) or "") or "无说明"
    parts = [stem, source, date, caption, unique_id]

    def filename():
        return "_".join(parts) + extension

    # Shorten captions first, then the original stem, then the source name.
    for index in (3, 0, 1):
        excess = len(filename().encode("utf-8")) - MAX_FILENAME_BYTES
        if excess <= 0:
            break
        first_char_size = len(parts[index][0].encode("utf-8"))
        budget = max(first_char_size, len(parts[index].encode("utf-8")) - excess)
        parts[index] = _shorten(parts[index], budget)
    if len(filename().encode("utf-8")) > MAX_FILENAME_BYTES:
        raise ValueError("File unique ID and extension exceed the filename limit")
    return filename()
