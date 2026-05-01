"""Pipeline module — orchestrates the full scrape → parse → clean → export flow."""

from __future__ import annotations

import logging
from typing import Any

from cleaner import Cleaner
from compliance import Compliance, ComplianceError
from config import AppConfig, load_config
from fetcher import Fetcher, FetchError
from data_parser import Parser
from saver import Saver

logger = logging.getLogger(__name__)


class Pipeline:
    """End-to-end data collection pipeline.

    Wires together ``Fetcher``, ``Parser``, ``Cleaner``, ``Compliance``,
    and ``Saver`` so that a single ``run()`` call handles the full lifecycle.

    Args:
        config_path: Path to the YAML configuration file.
    """

    def __init__(self, config_path: str = "config.yaml") -> None:
        self._config = load_config(config_path)
        self._fetcher = Fetcher(self._config)
        self._parser = Parser(self._config.parser.default_selector)
        self._cleaner = Cleaner(
            persist_dedup=self._config.cleaner.persist_dedup,
            persist_path=self._config.cleaner.persist_path,
        )
        self._compliance = Compliance(
            request_delay_min=self._config.compliance.request_delay_min,
            request_delay_max=self._config.compliance.request_delay_max,
            forbidden_keywords=self._config.compliance.forbidden_keywords,
            allowed_domains=self._config.compliance.allowed_domains,
        )
        self._saver = Saver(
            export_path=self._config.saver.export_path,
            log_level=self._config.saver.log_level,
            log_path=self._config.saver.log_path,
        )
        self._saver.setup_logging()

    # ── public methods ────────────────────────────────────────────

    def run(
        self,
        url: str,
        rules: dict[str, Any],
        dedup_fields: list[str] | None = None,
        export_format: str | None = None,
        **fetch_kwargs: Any,
    ) -> dict[str, Any]:
        """One-shot collection: request → parse → clean → filter → export.

        Args:
            url: Target URL.
            rules: Parsing rules passed to :class:`Parser`.
            dedup_fields: Fields for deduplication (default: config value).
            export_format: ``"xlsx"`` or ``"csv"`` (default: config value).
            **fetch_kwargs: Extra arguments forwarded to the fetcher.

        Returns:
            Dict with ``total``, ``success``, ``failed``, ``output_path``.
        """
        fmt = export_format or self._config.saver.export_format
        dedup = dedup_fields or self._config.cleaner.dedup_fields

        # Compliance: domain check
        try:
            self._compliance.check_domain(url)
        except ComplianceError as exc:
            logger.error("[Pipeline] %s", exc)
            return {"total": 0, "success": 0, "failed": 1, "output_path": ""}

        # Rate limit
        self._compliance.rate_limit()

        # Fetch
        try:
            response = self._fetcher.fetch(url, **fetch_kwargs)
        except FetchError as exc:
            logger.error("[Pipeline] Fetch failed: %s", exc)
            return {"total": 0, "success": 0, "failed": 1, "output_path": ""}

        # Parse
        try:
            records = self._parser.parse(response, rules)
        except Exception as exc:
            logger.error("[Pipeline] Parse error: %s", exc)
            records = []

        # Clean & dedup
        records = self._cleaner.process(records, dedup_fields=dedup)

        # Compliance: filter sensitive fields
        records = self._compliance.filter_fields(records)

        # Compliance: validate response content
        self._compliance.validate(records)

        # Export
        output_path = ""
        if records:
            try:
                output_path = self._saver.save(records, fmt=fmt)
            except Exception as exc:
                logger.error("[Pipeline] Export error: %s", exc)

        self._saver.summary(len(records), len(records), 0)
        return {
            "total": len(records),
            "success": len(records),
            "failed": 0 if records else 1,
            "output_path": output_path,
        }

    def run_multi(
        self,
        urls: list[str],
        rules: dict[str, Any],
        dedup_fields: list[str] | None = None,
        export_format: str | None = None,
    ) -> dict[str, Any]:
        """Batch-collect from multiple URLs, exporting one combined file.

        Args:
            urls: List of URLs to scrape.
            rules: Parsing rules.
            dedup_fields: Deduplication fields.
            export_format: Export format.

        Returns:
            Summary dict with aggregate statistics.
        """
        fmt = export_format or self._config.saver.export_format
        dedup = dedup_fields or self._config.cleaner.dedup_fields

        all_records: list[dict[str, Any]] = []
        failed = 0

        for url in urls:
            # Domain check
            try:
                self._compliance.check_domain(url)
            except ComplianceError as exc:
                logger.error("[Pipeline] %s", exc)
                failed += 1
                continue

            # Rate limit
            self._compliance.rate_limit()

            # Fetch
            try:
                response = self._fetcher.fetch(url)
            except FetchError as exc:
                logger.error("[Pipeline] Fetch failed: %s", exc)
                failed += 1
                continue

            # Parse
            try:
                records = self._parser.parse(response, rules)
            except Exception as exc:
                logger.error("[Pipeline] Parse error: %s", exc)
                records = []

            all_records.extend(records)

        # Clean & dedup across all URLs
        all_records = self._cleaner.process(all_records, dedup_fields=dedup)

        # Filter sensitive fields
        all_records = self._compliance.filter_fields(all_records)

        # Export
        output_path = ""
        if all_records:
            try:
                output_path = self._saver.save(all_records, fmt=fmt)
            except Exception as exc:
                logger.error("[Pipeline] Export error: %s", exc)

        total = len(all_records)
        self._saver.summary(total + failed, total, failed)
        return {
            "total": total + failed,
            "success": total,
            "failed": failed,
            "output_path": output_path,
        }

    @property
    def fetcher(self) -> Fetcher:
        """Access the internal fetcher (e.g. to inject a proxy provider)."""
        return self._fetcher

    def close(self) -> None:
        """Release resources held by the pipeline."""
        self._cleaner.close()
