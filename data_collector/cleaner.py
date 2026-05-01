"""Cleaner module — data cleaning, deduplication, and text normalization."""

from __future__ import annotations

import copy
import logging
import re
import sqlite3
import unicodedata
from typing import Any

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Patterns for invisible / control characters
_ZERO_WIDTH = re.compile(r"[​‌‍﻿]")
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MULTI_SPACE = re.compile(r" +")
_HTML_TAG = re.compile(r"<[^>]+>")


class Cleaner:
    """Clean, normalize, and deduplicate scraped records.

    Args:
        persist_dedup: If ``True``, persist deduplication identifiers
                       to a SQLite database so they survive restarts.
        persist_path: Path to the SQLite file for persistent dedup.
    """

    def __init__(self, persist_dedup: bool = False, persist_path: str = "dedup.db") -> None:
        self._persist_dedup = persist_dedup
        self._seen: set[str] = set()
        self._db_path = persist_path
        self._conn: sqlite3.Connection | None = None

        if persist_dedup:
            self._conn = sqlite3.connect(persist_path)
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS dedup (sig TEXT PRIMARY KEY)",
            )
            self._conn.commit()

    # ── public methods ────────────────────────────────────────────

    def clean(self, data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Remove HTML tags, whitespace, zero-width and control characters.

        Args:
            data: Raw records.

        Returns:
            A new list of dicts with string values cleaned.
        """
        return [
            {k: self._clean_value(v) for k, v in record.items()}
            for record in data
        ]

    def deduplicate(
        self,
        data: list[dict[str, Any]],
        fields: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Deduplicate records by the specified fields.

        Args:
            data: Records to deduplicate.
            fields: Field names to compose the dedup key.
                    ``None`` falls back to ``["url"]``.

        Returns:
            A new list containing only the first occurrence of each key.
        """
        fields = fields or ["url"]
        result: list[dict[str, Any]] = []

        for record in data:
            sig = self._signature(record, fields)
            if sig is None:
                # 如果去重字段不存在，保留该记录（不去重）
                result.append(record)
                continue
            if self._is_duplicate(sig):
                continue
            self._mark_seen(sig)
            result.append(record)

        return result

    def process(
        self,
        data: list[dict[str, Any]],
        dedup_fields: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """One-shot clean + deduplicate.

        Args:
            data: Raw records.
            dedup_fields: Fields used for deduplication.

        Returns:
            Cleaned, deduplicated records (new list).
        """
        cleaned = self.clean(data)
        return self.deduplicate(cleaned, dedup_fields)

    def reset_dedup(self) -> None:
        """Clear all deduplication records."""
        self._seen.clear()
        if self._conn:
            self._conn.execute("DELETE FROM dedup")
            self._conn.commit()
            logger.info("[Cleaner] Dedup records cleared")

    @staticmethod
    def format_text(text: str) -> str:
        """Normalize a single string: full-width to half-width, NFC, etc.

        Args:
            text: Input string.

        Returns:
            Normalized string.
        """
        # Full-width to half-width
        chars: list[str] = []
        for ch in text:
            code = ord(ch)
            # Full-width ASCII (0xFF01–0xFF5E) → half-width
            if 0xFF01 <= code <= 0xFF5E:
                chars.append(chr(code - 0xFEE0))
            # Full-width space
            elif code == 0x3000:
                chars.append(" ")
            else:
                chars.append(ch)
        text = "".join(chars)

        # NFC normalization
        text = unicodedata.normalize("NFC", text)

        # Remove remaining invisible symbols
        text = _ZERO_WIDTH.sub("", text)
        text = _CONTROL_CHARS.sub("", text)
        text = _MULTI_SPACE.sub(" ", text)

        return text.strip()

    def close(self) -> None:
        """Close the persistent SQLite connection if open."""
        if self._conn:
            self._conn.close()
            self._conn = None

    # ── internal helpers ──────────────────────────────────────────

    def _clean_value(self, value: Any) -> Any:
        """Apply all cleaning steps to a single value."""
        if not isinstance(value, str):
            return value
        # Strip HTML tags
        text = _HTML_TAG.sub("", value)
        # Strip whitespace
        text = text.strip()
        # Remove zero-width chars
        text = _ZERO_WIDTH.sub("", text)
        # Remove control chars
        text = _CONTROL_CHARS.sub("", text)
        # Compress spaces
        text = _MULTI_SPACE.sub(" ", text)
        return text

    def _signature(self, record: dict[str, Any], fields: list[str]) -> str | None:
        """Build a dedup key from the given fields."""
        parts: list[str] = []
        for f in fields:
            v = record.get(f)
            if v is None:
                return None
            parts.append(str(v))
        return "|".join(parts)

    def _is_duplicate(self, sig: str) -> bool:
        """Check whether *sig* has been seen before."""
        if sig in self._seen:
            return True
        if self._conn:
            row = self._conn.execute(
                "SELECT 1 FROM dedup WHERE sig = ?", (sig,),
            ).fetchone()
            return row is not None
        return False

    def _mark_seen(self, sig: str) -> None:
        """Record *sig* as seen."""
        self._seen.add(sig)
        if self._conn:
            try:
                self._conn.execute(
                    "INSERT OR IGNORE INTO dedup (sig) VALUES (?)", (sig,),
                )
                self._conn.commit()
            except sqlite3.Error as exc:
                logger.warning("[Cleaner] Failed to persist dedup sig: %s", exc)
