"""Proxy provider module.

Defines the abstract ``ProxyProvider`` interface and a simple
static-list-based implementation.
"""

from __future__ import annotations

import logging
import random
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class ProxyProvider(ABC):
    """Abstract interface for proxy pools.

    Concrete implementations manage a pool of proxies, returning one
    on each ``get_proxy()`` call and allowing callers to report
    failures via ``mark_bad()``.
    """

    @abstractmethod
    def get_proxy(self) -> dict:
        """Return the next proxy dict.

        Returns:
            ``{"http": "http://ip:port", "https": "http://ip:port"}``
        """
        pass

    @abstractmethod
    def mark_bad(self, proxy: dict) -> None:
        """Mark a proxy as unusable / failed.

        Args:
            proxy: The proxy dict previously returned by ``get_proxy()``.
        """
        pass


class SimpleProxyProvider(ProxyProvider):
    """A simple round-robin proxy provider backed by a static list.

    Args:
        proxies: List of proxy strings like ``"http://127.0.0.1:7890"``.
                 If empty, returns empty dict (no proxy).
    """

    def __init__(self, proxies: list[str] | None = None) -> None:
        self._proxies = list(proxies or [])
        self._bad: set[str] = set()
        self._index = 0

    def get_proxy(self) -> dict:
        """Return the next available proxy in round-robin order.

        Returns:
            ``{"http": url, "https": url}`` or ``{}`` if no proxy available.
        """
        available = [p for p in self._proxies if p not in self._bad]
        if not available:
            return {}
        proxy = available[self._index % len(available)]
        self._index += 1
        logger.debug("[ProxyProvider] Selected proxy: %s", proxy)
        return {"http": proxy, "https": proxy}

    def mark_bad(self, proxy: dict) -> None:
        """Add the proxy to the bad list.

        Args:
            proxy: Proxy dict to mark as bad.
        """
        url = proxy.get("http") or proxy.get("https")
        if url:
            self._bad.add(url)
            logger.info("[ProxyProvider] Marked proxy as bad: %s", url)
