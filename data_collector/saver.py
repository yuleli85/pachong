"""Saver module — data export and logging setup."""

from __future__ import annotations

import io
import logging
import os
import re
import sys
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


class Saver:
    """Export data to files and manage logging configuration.

    Args:
        export_path: Base directory for exported files.
        log_level: Logging level string (``"DEBUG"``, ``"INFO"``, …).
        log_path: Path to the log file.
    """

    def __init__(
        self,
        export_path: str = "output",
        log_level: str = "INFO",
        log_path: str = "logs/app.log",
    ) -> None:
        self._export_path = Path(export_path)
        self._log_path = log_path
        self._log_level = log_level
        self._setup = False

    # ── public methods ────────────────────────────────────────────

    def save(
        self,
        data: list[dict[str, Any]] | None = None,
        fmt: str = "xlsx",
        filename: str = "data",
        html: str | None = None,
        url: str = "",
        **kwargs: Any,
    ) -> str:
        """Unified export entry point.

        Args:
            data: Records to export (for xlsx/csv).
            fmt: ``"xlsx"``, ``"csv"``, or ``"word"``.
            filename: Base filename (extension is added automatically).
            html: Raw HTML content (for word format).
            url: Source URL (for word format metadata).
            **kwargs: Passed through to the format-specific method.

        Returns:
            Absolute path of the saved file.

        Raises:
            ValueError: If *fmt* is unsupported.
        """
        self._export_path.mkdir(parents=True, exist_ok=True)

        if fmt == "xlsx":
            return self.to_excel(data or [], filename, **kwargs)
        if fmt == "csv":
            return self.to_csv(data or [], filename, **kwargs)
        if fmt == "word":
            return self.to_word(html or "", url, filename, **kwargs)
        raise ValueError(f"Unsupported export format: {fmt}")

    def to_excel(
        self,
        data: list[dict[str, Any]],
        filename: str = "data",
        sheet_name: str = "Sheet1",
    ) -> str:
        """Export records to an Excel ``.xlsx`` file.

        Features:
        - Auto-adjusted column widths (max content width + 2)
        - Bold header, frozen top row, auto-filter enabled
        - Timestamp appended to filename

        Args:
            data: Records to export.
            filename: Base filename.
            sheet_name: Name of the Excel sheet.

        Returns:
            Absolute path of the generated file.
        """
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_name = f"{filename}_{ts}.xlsx"
        file_path = self._export_path / file_name

        self._export_path.mkdir(parents=True, exist_ok=True)

        df = pd.DataFrame(data)
        df.to_excel(
            str(file_path),
            sheet_name=sheet_name,
            index=False,
            engine="openpyxl",
        )

        # Auto column width, bold header, freeze top row, auto-filter
        from openpyxl import load_workbook

        wb = load_workbook(str(file_path))
        ws = wb.active
        if ws is None:
            raise RuntimeError("Failed to open workbook after writing")

        for col_cells in ws.columns:
            max_len = 0
            col_letter = col_cells[0].column_letter
            for cell in col_cells:
                if cell.value:
                    cell_len = len(str(cell.value))
                    max_len = max(max_len, cell_len)
                # Bold header
                if cell.row == 1:
                    from openpyxl.styles import Font
                    cell.font = Font(bold=True)
            adjusted = min(max_len + 2, 50)
            ws.column_dimensions[col_letter].width = adjusted

        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions

        wb.save(str(file_path))
        logger.info("[Saver] Exported %d records to %s", len(data), file_path)
        return str(file_path)

    def to_csv(
        self,
        data: list[dict[str, Any]],
        filename: str = "data",
    ) -> str:
        """Export records to a UTF-8-BOM CSV file.

        Args:
            data: Records to export.
            filename: Base filename.

        Returns:
            Absolute path of the generated file.
        """
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_name = f"{filename}_{ts}.csv"
        file_path = self._export_path / file_name

        self._export_path.mkdir(parents=True, exist_ok=True)

        df = pd.DataFrame(data)
        df.to_csv(str(file_path), index=False, encoding="utf-8-sig")

        logger.info("[Saver] Exported %d records to %s", len(data), file_path)
        return str(file_path)

    def to_word(
        self,
        html: str,
        url: str = "",
        filename: str = "data",
    ) -> str:
        """Save webpage body content to a Word ``.docx`` file.

        Extracts the page title and body text, removing navigation,
        scripts, and other non-content elements.

        Args:
            html: Raw HTML response text.
            url: Source URL (included as metadata).
            filename: Base filename.

        Returns:
            Absolute path of the generated file.
        """
        from bs4 import BeautifulSoup
        from docx import Document
        from docx.shared import Pt, RGBColor
        from docx.enum.text import WD_ALIGN_PARAGRAPH

        soup = BeautifulSoup(html, "lxml")

        # Remove non-content elements
        for tag in soup.find_all(["script", "style", "nav", "header", "footer", "aside", "noscript", "iframe", "svg"]):
            tag.decompose()

        # Extract title
        title_tag = soup.find("title")
        title = title_tag.get_text(strip=True) if title_tag else ""

        # Build a clean list of text paragraphs from body
        paragraphs: list[tuple[str, int]] = []  # (text, level)
        body = soup.find("body") or soup

        for elem in body.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote", "pre"]):
            text = elem.get_text(strip=True)
            if not text:
                continue
            level = 0
            tag_name = elem.name.lower()
            if tag_name.startswith("h") and tag_name[1].isdigit():
                level = int(tag_name[1])
            elif tag_name == "blockquote":
                level = 98
            elif tag_name == "pre":
                level = 99

            paragraphs.append((text, level))

        # Fallback: raw body text split by newlines
        if not paragraphs:
            raw_text = body.get_text(separator="\n", strip=True)
            for line in raw_text.split("\n"):
                line = line.strip()
                if line:
                    paragraphs.append((line, 0))

        # Write to Word
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_name = f"{filename}_{ts}.docx"
        file_path = self._export_path / file_name

        self._export_path.mkdir(parents=True, exist_ok=True)

        doc = Document()

        # Title
        if title:
            heading = doc.add_heading(title, level=1)
            heading.alignment = WD_ALIGN_PARAGRAPH.CENTER

        # Source URL
        if url:
            meta = doc.add_paragraph()
            run = meta.add_run(f"Source: {url}")
            run.font.size = Pt(9)
            run.font.color.rgb = RGBColor(0x80, 0x80, 0x80)

        doc.add_paragraph()

        # Content
        for text, level in paragraphs:
            if level == 0:
                p = doc.add_paragraph(text)
                p.paragraph_format.space_after = Pt(6)
            elif level == 98:
                p = doc.add_paragraph()
                run = p.add_run(text)
                run.italic = True
                run.font.size = Pt(10)
            elif level == 99:
                p = doc.add_paragraph()
                run = p.add_run(text)
                run.font.name = "Consolas"
                run.font.size = Pt(9)
            elif 1 <= level <= 9:
                doc.add_heading(text, level=min(level, 4))
            else:
                doc.add_paragraph(text)

        doc.save(str(file_path))
        logger.info("[Saver] Saved webpage content to %s (%d paragraphs)", file_path, len(paragraphs))
        return str(file_path)

    def to_database(
        self,
        data: list[dict[str, Any]],
        connection_string: str,
        table: str,
    ) -> None:
        """Write records to a relational database.

        .. note::  Reserved for V2 implementation.

        Args:
            data: Records to insert.
            connection_string: SQLAlchemy-compatible connection string.
            table: Target table name.
        """
        raise NotImplementedError("to_database is reserved for V2")

    def setup_logging(self) -> None:
        """Initialize the logging system (console + rotating file handler).

        Format: ``[2026-05-01 14:30:52] [INFO] [Fetcher] ...``
        Log file rotates daily, keeps 7 days.
        """
        if self._setup:
            return

        fmt = "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
        datefmt = "%Y-%m-%d %H:%M:%S"
        level = getattr(logging, self._log_level.upper(), logging.INFO)

        # Console handler — force GBK for Chinese Windows consoles
        _console_out = io.TextIOWrapper(sys.stdout.buffer, encoding="gbk", errors="replace", line_buffering=True)
        console = logging.StreamHandler(_console_out)
        console.setLevel(level)
        console.setFormatter(logging.Formatter(fmt, datefmt=datefmt))

        # File handler — daily rotation, keep 7 days
        log_file = Path(self._log_path)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = TimedRotatingFileHandler(
            str(log_file),
            when="midnight",
            interval=1,
            backupCount=7,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(logging.Formatter(fmt, datefmt=datefmt))

        root = logging.getLogger()
        root.setLevel(level)
        root.addHandler(console)
        root.addHandler(file_handler)

        self._setup = True
        logger.info("[Saver] Logging initialized (level=%s, file=%s)", self._log_level, log_file)

    @staticmethod
    def summary(total: int, success: int, failed: int) -> None:
        """Log a collection summary line.

        Args:
            total: Total number of records / URLs processed.
            success: Number of successful operations.
            failed: Number of failures.
        """
        logger.info("采集完成 | 总数:%d | 成功:%d | 失败:%d", total, success, failed)
