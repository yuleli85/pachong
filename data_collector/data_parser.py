"""Parser module — HTML and JSON data extraction."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from bs4 import BeautifulSoup
from lxml import etree

logger = logging.getLogger(__name__)


class Parser:
    """Extract structured data from HTML or JSON responses.

    Args:
        default_selector: ``"css"`` or ``"xpath"``.
    """

    def __init__(self, default_selector: str = "css") -> None:
        self._default_selector = default_selector

    # ── public methods ────────────────────────────────────────────

    def parse(
        self,
        response: Any,
        rules: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Auto-detect response type and dispatch to the right parser.

        Args:
            response: ``httpx.Response`` or a raw ``str`` / ``dict``.
            rules: Parsing rules (see module docstring for format).

        Returns:
            List of extracted records.
        """
        # Unwrap httpx.Response
        if hasattr(response, "text") and hasattr(response, "headers"):
            content_type = response.headers.get("content-type", "")
            if "json" in content_type:
                return self.parse_json(response.json(), rules)
            return self.parse_html(response.text, rules)

        if isinstance(response, dict):
            return self.parse_json(response, rules)
        if isinstance(response, str):
            return self.parse_html(response, rules)

        logger.warning("[Parser] Unrecognised response type: %s", type(response))
        return []

    def parse_html(
        self,
        html: str,
        rules: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Parse HTML according to declarative rules.

        Args:
            html: Raw HTML string.
            rules: Must contain ``container`` (selector info) and ``fields``.

        Returns:
            List of dicts, one per container element.
        """
        soup = BeautifulSoup(html, "lxml")
        container_cfg = rules.get("container", {})
        container_sel = container_cfg.get("selector", "")
        container_type = container_cfg.get("type", self._default_selector)

        if container_type == "xpath":
            items = self._xpath_select(soup, container_sel)
        else:
            items = soup.select(container_sel)

        if not items:
            logger.debug("[Parser] No container elements matched: %s", container_sel)
            return []

        fields = rules.get("fields", {})
        results: list[dict[str, Any]] = []

        for item in items:
            record: dict[str, Any] = {}
            for name, field_cfg in fields.items():
                selector = field_cfg.get("selector", "")
                ftype = field_cfg.get("type", self._default_selector)
                attr = field_cfg.get("attribute", "text")

                if ftype == "xpath":
                    nodes = self._xpath_select(item, selector)
                    value = self._extract_value(nodes, attr)
                else:
                    node = item.select_one(selector)
                    value = self._extract_single_value(node, attr)

                if value is None:
                    logger.warning(
                        "[Parser] 字段 '%s' 解析失败，已填充空值", name,
                    )
                    record[name] = ""
                else:
                    record[name] = value
            results.append(record)

        return results

    def parse_json(
        self,
        data: Any,
        mapping: dict[str, str],
    ) -> list[dict[str, Any]]:
        """Safely extract fields from a nested JSON structure.

        Supports ``[*]`` wildcard for array traversal.

        Args:
            data: Parsed JSON (dict or list).
            mapping: ``{"field_name": "path.to.field"}``.

        Returns:
            List of dicts.  When multiple arrays are traversed, the
            longest array determines the record count.
        """
        # Discover array paths and determine the iteration length
        array_paths: list[tuple[str, list[str]]] = []
        for field_name, path in mapping.items():
            parts = _split_path(path)
            array_idx = _first_array_index(parts)
            if array_idx is not None:
                resolved = _resolve_static(data, parts[:array_idx])
                if isinstance(resolved, list):
                    array_paths.append((field_name, parts))

        if array_paths:
            return self._parse_json_array(data, mapping, array_paths)

        # Simple scalar extraction
        record: dict[str, Any] = {}
        for field_name, path in mapping.items():
            record[field_name] = _safe_get(data, _split_path(path))
        return [record]

    def parse_auto(
        self,
        response: Any,
    ) -> list[dict[str, Any]]:
        """Auto-analyze and extract structured data from the response.

        Uses a multi-strategy approach (in priority order):
        1. Intercepted JSON API responses (Playwright)
        2. HTML ``<table>`` elements
        3. DOM grid/list patterns

        Args:
            response: ``httpx.Response`` (may have ``api_responses`` attached)
                      or raw HTML string.

        Returns:
            List of extracted records. Empty list if nothing found.
        """
        # Strategy 1: Try intercepted API JSON data first
        api_responses = getattr(response, "api_responses", None)
        if api_responses:
            records = self._extract_from_api_responses(api_responses)
            if records:
                logger.info(
                    "[Parser] 自动分析: 从拦截的 API 中提取 %d 条记录", len(records),
                )
                return records

        # Get HTML content
        if hasattr(response, "text"):
            html = response.text
        elif isinstance(response, str):
            html = response
        else:
            return []

        # Strategy 2: Extract HTML tables
        soup = BeautifulSoup(html, "lxml")
        records = self._extract_tables(soup)
        if records:
            logger.info(
                "[Parser] 自动分析: 从 HTML 表格中提取 %d 条记录", len(records),
            )
            return records

        # Strategy 3: Extract grid/list patterns
        records = self._extract_grid_patterns(soup)
        if records:
            logger.info(
                "[Parser] 自动分析: 从 DOM 网格模式中提取 %d 条记录", len(records),
            )
            return records

        logger.warning("[Parser] 自动分析: 未检测到结构化数据")
        return []

    # ── internal helpers ──────────────────────────────────────────

    @staticmethod
    def _extract_value(nodes: list, attr: str) -> str | None:
        """Extract text or attribute from a list of lxml nodes."""
        if not nodes:
            return None
        node = nodes[0]
        if attr == "text":
            return "".join(node.itertext()).strip() or None
        return node.get(attr)

    @staticmethod
    def _extract_single_value(node: Any, attr: str) -> str | None:
        """Extract text or attribute from a single BeautifulSoup element."""
        if node is None:
            return None
        if attr == "text":
            text = node.get_text(strip=True)
            return text or None
        return node.get(attr)

    @staticmethod
    def _xpath_select(root: Any, expr: str) -> list:
        """Run an XPath expression against a root element."""
        from lxml.etree import _Element
        if isinstance(root, _Element):
            # Already an lxml element — run XPath directly (preserves context for .// paths)
            return root.xpath(expr)
        tree = etree.HTML(str(root))
        return tree.xpath(expr)

    def _parse_json_array(
        self,
        data: Any,
        mapping: dict[str, str],
        array_paths: list[tuple[str, list[str]]],
    ) -> list[dict[str, Any]]:
        """Handle JSON parsing when at least one path contains ``[*]``."""
        # Find the longest array
        max_len = 0
        for _, parts in array_paths:
            idx = _first_array_index(parts)
            resolved = _resolve_static(data, parts[:idx])
            if isinstance(resolved, list):
                max_len = max(max_len, len(resolved))

        results: list[dict[str, Any]] = []
        for i in range(max_len):
            record: dict[str, Any] = {}
            for field_name, path in mapping.items():
                parts = _split_path(path)
                # Replace [*] with the current index
                resolved_parts: list[str | int] = []
                for p in parts:
                    if p == "*":
                        resolved_parts.append(i)
                    else:
                        resolved_parts.append(p)
                record[field_name] = _safe_get(data, resolved_parts)
            results.append(record)
        return results

    # ── Auto-extraction helpers ───────────────────────────────────

    @staticmethod
    def _extract_from_api_responses(
        api_responses: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Extract the largest JSON array from intercepted API responses."""
        # Try each API response, return the one with the most records
        for api in api_responses:
            data = api.get("data")
            if isinstance(data, list) and data and isinstance(data[0], dict):
                return data
            if isinstance(data, dict):
                # Look for the largest list inside the dict
                result = _find_largest_array(data)
                if result:
                    return result
        return []

    @staticmethod
    def _extract_tables(soup: Any) -> list[dict[str, Any]]:
        """Extract data from HTML <table> elements."""
        # Find the table with the most rows
        best_table = None
        best_rows = 0
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) > best_rows:
                best_rows = len(rows)
                best_table = table

        if not best_table or best_rows < 2:
            return []

        all_rows = best_table.find_all("tr")

        # Extract headers: try <th> elements first, then first row
        headers = []
        th_cells = all_rows[0].find_all("th")
        if th_cells:
            headers = [th.get_text(strip=True) for th in th_cells]
        else:
            # Use first row's <td> as headers
            td_cells = all_rows[0].find_all("td")
            if td_cells:
                headers = [td.get_text(strip=True) for td in td_cells]
            else:
                return []

        # Sanitize header names (make valid keys)
        safe_headers = [_sanitize_header(h) for h in headers]

        # Determine where data starts
        data_start = 0 if not th_cells else 1

        # Extract data rows
        results: list[dict[str, Any]] = []
        for row in all_rows[data_start:]:
            cells = row.find_all(["td", "th"])
            if not cells:
                continue
            record: dict[str, Any] = {}
            for i, header in enumerate(safe_headers):
                if i < len(cells):
                    record[header] = cells[i].get_text(strip=True)
                else:
                    record[header] = ""
            # Skip rows that are all empty
            if any(v for v in record.values()):
                results.append(record)

        return results

    @staticmethod
    def _extract_grid_patterns(soup: Any) -> list[dict[str, Any]]:
        """Detect repeated element patterns (ul>li, div.grid>div.item, etc.)."""
        # Look for list containers with repeated items that have multiple children
        candidates: list[tuple[str, int]] = []
        for tag in ["ul", "ol", "dl"]:
            for container in soup.find_all(tag):
                children = container.find_all(["li", "dt", "dd"], recursive=False)
                if len(children) >= 3:
                    candidates.append((tag, len(children)))

        if not candidates:
            return []

        # Use the container with the most items
        candidates.sort(key=lambda c: c[1], reverse=True)
        tag_name, count = candidates[0]

        container = soup.find(tag_name)
        if not container:
            return []

        items = container.find_all([tag_name[:-1] if tag_name.endswith("s") else tag_name], recursive=False)
        # Try li, dt, dd
        if not items:
            items = container.find_all(["li", "dt", "dd"], recursive=False)

        results: list[dict[str, Any]] = []
        for idx, item in enumerate(items):
            text = item.get_text(strip=True)
            if text:
                results.append({"index": str(idx + 1), "text": text})

        return results


def _sanitize_header(name: str) -> str:
    """Convert a header string to a valid dict key."""
    name = name.strip().strip("*").strip()
    # Replace spaces and special chars with underscores
    name = re.sub(r"[^\w一-鿿]+", "_", name, flags=re.UNICODE)
    name = name.strip("_")
    # Ensure unique keys
    return name or "col"


def _find_largest_array(data: Any) -> list[dict[str, Any]] | None:
    """Recursively find the largest list of dicts inside a dict."""
    if not isinstance(data, dict):
        return None

    best: list[dict[str, Any]] | None = None
    best_len = 0

    for value in data.values():
        if isinstance(value, list) and len(value) > best_len:
            if value and isinstance(value[0], dict):
                best = value
                best_len = len(value)
        elif isinstance(value, dict):
            nested = _find_largest_array(value)
            if nested and len(nested) > best_len:
                best = nested
                best_len = len(nested)

    return best


# ── Path helpers ───────────────────────────────────────────────────

def _split_path(path: str) -> list[str | int]:
    """``"data.items[*].name"`` → ``["data", "items", "*", "name"]``."""
    parts: list[str | int] = []
    for seg in path.split("."):
        seg = seg.strip()
        if seg == "[*]":
            parts.append("*")
        elif seg.endswith("[*]"):
            # Handle "items[*]" → ["items", "*"]
            parts.append(seg[:-3])
            parts.append("*")
        elif seg.startswith("[") and seg.endswith("]"):
            try:
                parts.append(int(seg[1:-1]))
            except ValueError:
                parts.append(seg)
        else:
            parts.append(seg)
    return parts


def _first_array_index(parts: list[str | int]) -> int | None:
    """Return the index of the first ``"*"`` in *parts*."""
    for i, p in enumerate(parts):
        if p == "*":
            return i
    return None


def _resolve_static(data: Any, parts: list[str | int]) -> Any:
    """Navigate *data* following *parts* (no ``"*"``)."""
    current = data
    for p in parts:
        if isinstance(current, dict) and isinstance(p, str):
            current = current.get(p)
        elif isinstance(current, list) and isinstance(p, int):
            current = current[p] if 0 <= p < len(current) else None
        else:
            return None
    return current


def _safe_get(data: Any, parts: list[str | int]) -> Any:
    """Safely traverse *data*, returning ``None`` on any missing key."""
    current = data
    for p in parts:
        if isinstance(current, dict) and isinstance(p, str):
            current = current.get(p)
        elif isinstance(current, list) and isinstance(p, int):
            current = current[p] if 0 <= p < len(current) else None
        elif isinstance(current, list) and isinstance(p, str) and p == "*":
            return current
        else:
            return None
    return current
