"""Tests for the Saver module."""

import sys
import os
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from saver import Saver


@pytest.fixture
def saver():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Saver(export_path=tmpdir, log_level="WARNING")


SAMPLE_DATA = [
    {"name": "Alice", "age": 30},
    {"name": "Bob", "age": 25},
]


class TestExcelExport:
    def test_creates_xlsx_file(self, saver):
        path = saver.to_excel(SAMPLE_DATA, "test_data")
        assert os.path.isfile(path)
        assert path.endswith(".xlsx")

    def test_filename_includes_timestamp(self, saver):
        path = saver.to_excel(SAMPLE_DATA, "report")
        filename = os.path.basename(path)
        # Should match pattern: report_YYYYMMDD_HHMMSS.xlsx
        assert filename.startswith("report_")
        assert filename.endswith(".xlsx")


class TestCsvExport:
    def test_creates_csv_file(self, saver):
        path = saver.to_csv(SAMPLE_DATA, "test_data")
        assert os.path.isfile(path)
        assert path.endswith(".csv")

    def test_csv_has_bom(self, saver):
        path = saver.to_csv(SAMPLE_DATA, "test_data")
        with open(path, "rb") as f:
            bom = f.read(3)
        assert bom == b"\xef\xbb\xbf"


class TestSaveDispatch:
    def test_dispatch_to_excel(self, saver):
        path = saver.save(SAMPLE_DATA, fmt="xlsx", filename="dispatch")
        assert path.endswith(".xlsx")

    def test_dispatch_to_csv(self, saver):
        path = saver.save(SAMPLE_DATA, fmt="csv", filename="dispatch")
        assert path.endswith(".csv")

    def test_dispatch_to_word(self, saver):
        html = "<html><head><title>Test</title></head><body><p>Hello</p></body></html>"
        path = saver.save(fmt="word", html=html, url="https://example.com", filename="dispatch")
        assert path.endswith(".docx")

    def test_invalid_format_raises(self, saver):
        with pytest.raises(ValueError, match="Unsupported export format"):
            saver.save(SAMPLE_DATA, fmt="json")


class TestWordExport:
    SAMPLE_HTML = (
        "<html><head><title>Test Page</title></head><body>"
        "<h1>Main Title</h1>"
        "<p>First paragraph.</p>"
        "<h2>Subsection</h2>"
        "<p>Second paragraph.</p>"
        "<script>alert('skip')</script>"
        "<footer>skip this</footer>"
        "</body></html>"
    )

    def test_creates_docx_file(self, saver):
        path = saver.to_word(self.SAMPLE_HTML, "https://example.com", "test")
        assert os.path.isfile(path)
        assert path.endswith(".docx")

    def test_filename_includes_timestamp(self, saver):
        path = saver.to_word(self.SAMPLE_HTML, "https://example.com", "report")
        filename = os.path.basename(path)
        assert filename.startswith("report_")
        assert filename.endswith(".docx")

    def test_creates_valid_docx(self, saver):
        from docx import Document
        path = saver.to_word(self.SAMPLE_HTML, "https://example.com", "test")
        doc = Document(path)
        # Should have at least title + content paragraphs
        assert len(doc.paragraphs) > 2

    def test_empty_html_still_creates_file(self, saver):
        path = saver.to_word("<html><body></body></html>", "", "empty")
        assert os.path.isfile(path)
        assert path.endswith(".docx")
