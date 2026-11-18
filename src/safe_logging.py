"""Logging helpers with no configuration or Telegram import side effects."""

import logging
import re


def redact(value):
    return re.sub(r"\b\d{6,}:[A-Za-z0-9_-]{20,}", "[BOT_TOKEN]", str(value))


def safe_error(error):
    return redact(f"{type(error).__name__}: {error}")[:1000]


class RedactingFormatter(logging.Formatter):
    def format(self, record):
        return redact(super().format(record))
