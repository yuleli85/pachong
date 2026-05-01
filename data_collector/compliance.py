"""Compliance module — rate limiting, sensitive-field filtering, domain validation."""

from __future__ import annotations

import logging
import random
import re
import time
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class ComplianceError(Exception):
    """Raised when a compliance check fails."""
    pass


# Regex patterns for detecting sensitive data in response values
_SENSITIVE_PATTERNS: dict[str, re.Pattern] = {
    "id_card": re.compile(r"\b\d{17}[\dXx]\b"),
    "phone": re.compile(r"\b1[3-9]\d{9}\b"),
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
}


class Compliance:
    """Enforce rate limiting, domain whitelisting, and sensitive-data checks.

    Args:
        request_delay_min: Minimum delay between requests (seconds).
        request_delay_max: Maximum delay between requests (seconds).
        forbidden_keywords: Field keys containing any of these will be removed.
        allowed_domains: If non-empty, only URLs under these domains are allowed.
    """

    def __init__(
        self,
        request_delay_min: float = 1.0,
        request_delay_max: float = 3.0,
        forbidden_keywords: list[str] | None = None,
        allowed_domains: list[str] | None = None,
    ) -> None:
        self._delay_min = request_delay_min
        self._delay_max = request_delay_max
        self._forbidden_keywords = [kw.lower() for kw in (forbidden_keywords or [])]
        self._allowed_domains = [d.lower() for d in (allowed_domains or [])]

    # ── public methods ────────────────────────────────────────────

    def rate_limit(self) -> None:
        """Sleep for a random duration within the configured delay range.

        Must be called before every outgoing request.
        """
        delay = random.uniform(self._delay_min, self._delay_max)
        logger.debug("[Compliance] Rate limit: sleeping %.2f s", delay)
        time.sleep(delay)

    def set_delay_range(self, min_delay: float, max_delay: float) -> None:
        """Dynamically adjust the request delay range.

        Args:
            min_delay: Minimum delay in seconds.
            max_delay: Maximum delay in seconds.
        """
        self._delay_min = min_delay
        self._delay_max = max_delay

    def check_domain(self, url: str) -> bool:
        """Validate that *url* belongs to an allowed domain.

        Args:
            url: The URL to validate.

        Returns:
            ``True`` if the domain is allowed.

        Raises:
            ComplianceError: Domain is not in the whitelist.
        """
        if not self._allowed_domains:
            return True

        domain = urlparse(url).hostname or ""
        domain = domain.lower()

        for allowed in self._allowed_domains:
            if domain == allowed or domain.endswith("." + allowed):
                return True

        raise ComplianceError(
            f"Domain not in whitelist: {domain} (url={url})"
        )

    def filter_fields(
        self,
        data: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Remove fields whose key contains a forbidden keyword.

        Matching is case-insensitive and uses substring containment.
        E.g. ``"user_phone"`` matches keyword ``"phone"``.

        Args:
            data: Records to filter.

        Returns:
            New list of dicts with sensitive fields removed.
        """
        if not self._forbidden_keywords:
            return data

        results: list[dict[str, Any]] = []
        for record in data:
            cleaned: dict[str, Any] = {}
            for key, value in record.items():
                key_lower = key.lower()
                if any(kw in key_lower for kw in self._forbidden_keywords):
                    logger.info("[Compliance] 已过滤敏感字段: %s", key)
                else:
                    cleaned[key] = value
            results.append(cleaned)
        return results

    def validate(self, response: Any) -> list[str]:
        """Inspect response data for values matching sensitive patterns.

        Does **not** auto-delete — it emits a ``WARNING`` and returns
        a list of matched pattern names so the caller can decide.

        Args:
            response: A single record dict or a list of record dicts.

        Returns:
            List of pattern names that matched at least once.
        """
        if isinstance(response, dict):
            records = [response]
        elif isinstance(response, list):
            records = response
        else:
            return []

        matched_patterns: set[str] = set()

        for record in records:
            for value in record.values():
                if not isinstance(value, str):
                    continue
                for name, pattern in _SENSITIVE_PATTERNS.items():
                    if pattern.search(value):
                        matched_patterns.add(name)
                        logger.warning(
                            "[Compliance] 检测到疑似敏感数据模式: %s (值=%s…)",
                            name, value[:20],
                        )

        return list(matched_patterns)
