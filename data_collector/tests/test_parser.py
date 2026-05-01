"""Tests for the Parser module."""

import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from data_parser import Parser


@pytest.fixture
def parser():
    return Parser(default_selector="css")


# ── HTML parsing ─────────────────────────────────────────────────────

HTML_SAMPLE = """
<html>
<body>
  <div class="article-item">
    <h2 class="title">Article One</h2>
    <a href="/link1">Read</a>
    <span class="date">2026-05-01</span>
  </div>
  <div class="article-item">
    <h2 class="title">Article Two</h2>
    <a href="/link2">Read</a>
    <span class="date">2026-05-02</span>
  </div>
</body>
</html>
"""

HTML_RULES = {
    "container": {"selector": "div.article-item", "type": "css"},
    "fields": {
        "title": {"selector": "h2.title", "type": "css", "attribute": "text"},
        "link": {"selector": "a", "type": "css", "attribute": "href"},
        "date": {"selector": "span.date", "type": "css", "attribute": "text"},
    },
}


class TestHtmlParsing:
    def test_basic_extraction(self, parser):
        results = parser.parse_html(HTML_SAMPLE, HTML_RULES)
        assert len(results) == 2
        assert results[0]["title"] == "Article One"
        assert results[0]["link"] == "/link1"
        assert results[0]["date"] == "2026-05-01"

    def test_missing_field_filled_empty(self, parser):
        rules_missing = {
            "container": {"selector": "div.article-item", "type": "css"},
            "fields": {
                "title": {"selector": "h2.title", "type": "css", "attribute": "text"},
                "nonexistent": {"selector": "div.ghost", "type": "css", "attribute": "text"},
            },
        }
        results = parser.parse_html(HTML_SAMPLE, rules_missing)
        assert results[0]["title"] == "Article One"
        assert results[0]["nonexistent"] == ""

    def test_empty_container_returns_empty_list(self, parser):
        rules = {
            "container": {"selector": "div.no-such-class", "type": "css"},
            "fields": {"title": {"selector": "h2", "type": "css", "attribute": "text"}},
        }
        assert parser.parse_html(HTML_SAMPLE, rules) == []

    def test_attribute_extraction(self, parser):
        rules = {
            "container": {"selector": "div.article-item", "type": "css"},
            "fields": {
                "href": {"selector": "a", "type": "css", "attribute": "href"},
                "link_text": {"selector": "a", "type": "css", "attribute": "text"},
            },
        }
        results = parser.parse_html(HTML_SAMPLE, rules)
        assert results[0]["href"] == "/link1"
        assert results[0]["link_text"] == "Read"


# ── JSON parsing ─────────────────────────────────────────────────────

class TestJsonParsing:
    def test_scalar_extraction(self, parser):
        data = {"name": "Alice", "age": 30}
        mapping = {"name": "name", "age": "age"}
        results = parser.parse_json(data, mapping)
        assert len(results) == 1
        assert results[0]["name"] == "Alice"
        assert results[0]["age"] == 30

    def test_nested_path(self, parser):
        data = {"data": {"user": {"name": "Bob"}}}
        mapping = {"name": "data.user.name"}
        results = parser.parse_json(data, mapping)
        assert results[0]["name"] == "Bob"

    def test_missing_path_returns_none(self, parser):
        data = {"key": "value"}
        mapping = {"missing": "deeply.nested.path"}
        results = parser.parse_json(data, mapping)
        assert results[0]["missing"] is None

    def test_array_traversal(self, parser):
        data = {
            "items": [
                {"id": 1, "name": "A"},
                {"id": 2, "name": "B"},
            ]
        }
        mapping = {"id": "items[*].id", "name": "items[*].name"}
        results = parser.parse_json(data, mapping)
        assert len(results) == 2
        assert results[0]["name"] == "A"
        assert results[1]["name"] == "B"

    def test_empty_data_returns_empty_list(self, parser):
        results = parser.parse_json({}, {"x": "y"})
        # Missing fields return None values, not empty list
        assert results == [{"x": None}]


# ── Auto-dispatch (parse) ───────────────────────────────────────────

class TestParseDispatch:
    def test_dispatches_html_from_string(self, parser):
        results = parser.parse(HTML_SAMPLE, HTML_RULES)
        assert len(results) == 2

    def test_dispatches_json_from_dict(self, parser):
        data = {"items": [{"id": 1, "name": "A"}]}
        mapping = {"id": "items[*].id", "name": "items[*].name"}
        results = parser.parse(data, mapping)
        assert len(results) == 1

    def test_unrecognised_type_returns_empty(self, parser):
        assert parser.parse(12345, {}) == []
