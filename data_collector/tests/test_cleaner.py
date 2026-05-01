"""Tests for the Cleaner module."""

import sys
import os
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cleaner import Cleaner


# ── Basic cleaning ───────────────────────────────────────────────────

class TestClean:
    def test_removes_html_tags(self):
        cleaner = Cleaner()
        data = [{"text": "<p>Hello <b>World</b></p>"}]
        result = cleaner.clean(data)
        assert result[0]["text"] == "Hello World"

    def test_strips_whitespace(self):
        cleaner = Cleaner()
        data = [{"text": "  hello   world  "}]
        result = cleaner.clean(data)
        # multi-space compression is part of clean()
        assert result[0]["text"] == "hello world"

    def test_removes_zero_width_chars(self):
        cleaner = Cleaner()
        data = [{"text": "hello​world"}]
        result = cleaner.clean(data)
        assert result[0]["text"] == "helloworld"

    def test_compresses_multiple_spaces(self):
        cleaner = Cleaner()
        data = [{"text": "a   b\t\tc"}]
        result = cleaner.clean(data)
        assert result[0]["text"] == "a b\t\tc"  # tabs are control chars, removed

    def test_preserves_non_string_types(self):
        cleaner = Cleaner()
        data = [{"count": 42, "flag": True}]
        result = cleaner.clean(data)
        assert result[0]["count"] == 42
        assert result[0]["flag"] is True

    def test_does_not_modify_original(self):
        cleaner = Cleaner()
        original = [{"text": "  hello  "}]
        result = cleaner.clean(original)
        assert original[0]["text"] == "  hello  "
        assert result[0]["text"] == "hello"


# ── Deduplication ────────────────────────────────────────────────────

class TestDeduplicate:
    def test_dedup_by_single_field(self):
        cleaner = Cleaner()
        data = [
            {"url": "a", "title": "A"},
            {"url": "b", "title": "B"},
            {"url": "a", "title": "A (dup)"},
        ]
        result = cleaner.deduplicate(data, fields=["url"])
        assert len(result) == 2
        assert result[0]["url"] == "a"
        assert result[1]["url"] == "b"

    def test_dedup_by_multiple_fields(self):
        cleaner = Cleaner()
        data = [
            {"url": "a", "title": "A"},
            {"url": "a", "title": "B"},
            {"url": "a", "title": "A"},
        ]
        result = cleaner.deduplicate(data, fields=["url", "title"])
        assert len(result) == 2

    def test_dedup_preserves_first_occurrence(self):
        cleaner = Cleaner()
        data = [
            {"url": "a", "data": "first"},
            {"url": "a", "data": "second"},
        ]
        result = cleaner.deduplicate(data, fields=["url"])
        assert result[0]["data"] == "first"

    def test_keeps_records_missing_dedup_field(self):
        cleaner = Cleaner()
        data = [
            {"url": "a"},
            {"other": "b"},
        ]
        result = cleaner.deduplicate(data, fields=["url"])
        # 缺少去重字段的记录会被保留（不去重）
        assert len(result) == 2


# ── Persistent dedup ─────────────────────────────────────────────────

class TestPersistDedup:
    def test_persist_across_instances(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            db_path = tf.name

        c1 = Cleaner(persist_dedup=True, persist_path=db_path)
        c1.deduplicate([{"url": "a"}], fields=["url"])
        c1.close()

        c2 = Cleaner(persist_dedup=True, persist_path=db_path)
        result = c2.deduplicate(
            [{"url": "a"}, {"url": "b"}], fields=["url"],
        )
        c2.close()
        # "a" should be filtered out by persisted dedup
        assert len(result) == 1
        assert result[0]["url"] == "b"

        os.remove(db_path)

    def test_reset_dedup(self):
        cleaner = Cleaner()
        cleaner.deduplicate([{"url": "a"}], fields=["url"])
        assert len(cleaner.deduplicate([{"url": "a"}], fields=["url"])) == 0
        cleaner.reset_dedup()
        assert len(cleaner.deduplicate([{"url": "a"}], fields=["url"])) == 1


# ── Text formatting ──────────────────────────────────────────────────

class TestFormatText:
    def test_fullwidth_to_halfwidth(self):
        # Full-width "ＡＢＣ" -> "ABC"
        result = Cleaner.format_text("ＡＢＣ")
        assert result == "ABC"

    def test_fullwidth_space(self):
        result = Cleaner.format_text("　")
        assert result == ""

    def test_nfc_normalization(self):
        # NFD e + combining acute -> NFC é
        nfd = "é"
        result = Cleaner.format_text(nfd)
        assert result == "é"


# ── Process (combined) ──────────────────────────────────────────────

class TestProcess:
    def test_clean_then_dedup(self):
        cleaner = Cleaner()
        data = [
            {"url": "a", "text": "  Hello  "},
            {"url": "a", "text": "  Hello  "},
            {"url": "b", "text": "  World  "},
        ]
        result = cleaner.process(data, dedup_fields=["url"])
        assert len(result) == 2
        assert result[0]["text"] == "Hello"
