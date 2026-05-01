"""Fetcher module — HTTP request handling with retry, proxy, and UA rotation."""

from __future__ import annotations

import logging
import random
import time
from typing import Any

import httpx

from config import AppConfig, _DEFAULT_USER_AGENTS
from proxy_provider import ProxyProvider

logger = logging.getLogger(__name__)


# ── Custom exceptions ─────────────────────────────────────────────

class CollectorError(Exception):
    """Base exception for all collector errors."""
    pass


class FetchError(CollectorError):
    """Raised when a request fails after all retries."""
    pass


class ProxyError(CollectorError):
    """Raised when a proxy is invalid or unavailable."""
    pass


# ── Fetcher ───────────────────────────────────────────────────────

class Fetcher:
    """High-level HTTP client with retry, proxy rotation, and UA pooling.

    Args:
        config: Application configuration instance.
    """

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._proxy_provider: ProxyProvider | None = None
        self._user_agents: list[str] = list(_DEFAULT_USER_AGENTS)

    # ── public methods ────────────────────────────────────────────

    def set_proxy_provider(self, provider: ProxyProvider) -> None:
        """Inject a proxy provider instance.

        Args:
            provider: Any ``ProxyProvider`` implementation.
        """
        self._proxy_provider = provider

    def fetch(
        self,
        url: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        proxy: dict[str, str] | None = None,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """Unified request entry point with automatic retry and backoff.

        Args:
            url: Target URL.
            method: HTTP method (GET / POST / ...).
            headers: Optional custom headers (merged over defaults).
            cookies: Optional cookies.
            proxy: Static proxy dict, overrides injected provider.
            timeout: Override the configured timeout for this call.
            **kwargs: Additional arguments passed to the httpx request.

        Returns:
            ``httpx.Response`` on success.

        Raises:
            FetchError: All retries exhausted.
        """
        max_retries = self._config.fetcher.max_retries
        backoff_base = self._config.fetcher.retry_backoff_base
        effective_timeout = timeout or self._config.fetcher.timeout

        last_exc: Exception | None = None

        for attempt in range(max_retries + 1):
            try:
                response = self._request(
                    url, method, headers, cookies, proxy, effective_timeout, **kwargs,
                )
                logger.info(
                    "[Fetcher] %s %s -> %d (%.1fs)",
                    method.upper(), url, response.status_code, response.elapsed.total_seconds(),
                )
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as exc:
                if 500 <= exc.response.status_code < 600:
                    last_exc = exc
                    self._retry_log(method, url, exc, attempt, max_retries)
                else:
                    raise FetchError(
                        f"{method.upper()} {url} -> HTTP {exc.response.status_code}"
                    ) from exc
            except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
                last_exc = exc
                self._retry_log(method, url, exc, attempt, max_retries)

            if attempt < max_retries:
                delay = backoff_base ** attempt + random.random()
                logger.debug("[Fetcher] Retrying in %.2f s …", delay)
                time.sleep(delay)

        raise FetchError(
            f"{method.upper()} {url} failed after {max_retries + 1} attempts"
        ) from last_exc

    def get(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        proxy: dict[str, str] | None = None,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """Send a GET request.

        Args:
            url: Target URL.
            headers: Optional custom headers.
            cookies: Optional cookies.
            proxy: Static proxy dict.
            timeout: Per-call timeout override.

        Returns:
            ``httpx.Response``.
        """
        return self.fetch(
            url, "GET", headers=headers, cookies=cookies,
            proxy=proxy, timeout=timeout, **kwargs,
        )

    def post(
        self,
        url: str,
        data: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        proxy: dict[str, str] | None = None,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """Send a POST request.

        Args:
            url: Target URL.
            data: Form-encoded body.
            json: JSON body.
            headers: Optional custom headers.
            proxy: Static proxy dict.
            timeout: Per-call timeout override.

        Returns:
            ``httpx.Response``.
        """
        return self.fetch(
            url, "POST", headers=headers, proxy=proxy,
            timeout=timeout, data=data, json=json, **kwargs,
        )

    def fetch_api(self, url: str, **kwargs: Any) -> dict[str, Any]:
        """Send a GET request and return parsed JSON.

        Args:
            url: API endpoint URL.
            **kwargs: Passed to :meth:`fetch`.

        Returns:
            Parsed JSON as a dict.

        Raises:
            FetchError: On failure or non-JSON response.
        """
        resp = self.fetch(url, "GET", **kwargs)
        try:
            return resp.json()
        except ValueError as exc:
            raise FetchError(f"Invalid JSON from {url}") from exc

    # ── internal helpers ──────────────────────────────────────────

    def _request(
        self,
        url: str,
        method: str,
        headers: dict[str, str] | None,
        cookies: dict[str, str] | None,
        proxy: dict[str, str] | None,
        timeout: float,
        **kwargs: Any,
    ) -> httpx.Response:
        """Low-level request, single attempt (no retry)."""
        req_headers = self._build_headers(headers)
        effective_proxy = self._resolve_proxy(proxy)

        with httpx.Client(
            proxy=self._proxy_url(effective_proxy),
            timeout=timeout,
            follow_redirects=True,
        ) as client:
            return client.request(
                method.upper(),
                url,
                headers=req_headers,
                cookies=cookies or {},
                **kwargs,
            )

    def _build_headers(self, custom: dict[str, str] | None) -> dict[str, str]:
        """Merge default headers with caller-supplied ones."""
        headers: dict[str, str] = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
        }
        if self._config.fetcher.user_agent_rotation:
            headers["User-Agent"] = random.choice(self._user_agents)
        else:
            headers["User-Agent"] = self._user_agents[0]
        if custom:
            headers.update(custom)
        return headers

    def _resolve_proxy(self, explicit: dict[str, str] | None) -> dict[str, str]:
        """Determine which proxy to use for the current request."""
        if explicit:
            return explicit
        if self._proxy_provider:
            return self._proxy_provider.get_proxy()
        return {}

    @staticmethod
    def _proxy_url(proxy: dict[str, str]) -> str | None:
        """Extract a single proxy URL from a proxy dict."""
        return proxy.get("https") or proxy.get("http") or None

    @staticmethod
    def _retry_log(
        method: str,
        url: str,
        exc: Exception,
        attempt: int,
        max_retries: int,
    ) -> None:
        logger.error(
            "[Fetcher] %s %s -> %s (retry %d/%d)",
            method.upper(), url, type(exc).__name__,
            attempt + 1, max_retries,
        )


# ── Dynamic Fetcher (Playwright) ──────────────────────────────────

class DynamicFetcher:
    """Fetch page content using Playwright for JavaScript-rendered pages.

    Args:
        config: Application configuration instance.
    """

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._user_agents: list[str] = list(_DEFAULT_USER_AGENTS)
        self._browser = None
        self._dynamic_wait_time = getattr(config.fetcher, 'dynamic_wait_time', 3.0)

    def fetch(self, url: str, wait_time: float | None = None, **kwargs: Any) -> httpx.Response:
        """Navigate to *url*, wait for JS rendering, return the rendered HTML.

        Intercepts all XHR/Fetch JSON API responses during page load and
        attaches them as ``response.api_responses`` for auto-analysis.

        Args:
            url: Target URL.
            wait_time: Seconds to wait for JavaScript to finish.
                       ``None`` uses the config default (``dynamic_wait_time``).
            **kwargs: Currently unused, reserved for future options.

        Returns:
            An ``httpx.Response``-like object with ``text``, ``status_code``,
            ``headers``, and ``elapsed`` attributes. Also has
            ``api_responses`` attribute with intercepted JSON data.

        Raises:
            FetchError: If Playwright is not installed or navigation fails.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise FetchError(
                "Playwright not installed. Run: pip install playwright && playwright install"
            ) from None

        effective_wait = wait_time if wait_time is not None else self._dynamic_wait_time

        start = time.time()
        api_responses: list[dict[str, Any]] = []

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=random.choice(self._user_agents),
                viewport={"width": 1920, "height": 1080},
            )
            page = context.new_page()

            # Intercept responses to collect JSON API data
            def _handle_response(response_obj: Any) -> None:
                """Callback for each network response."""
                res_url = response_obj.url
                res_status = response_obj.status
                res_type = response_obj.request.resource_type

                # Only collect XHR/fetch responses that return JSON
                if res_type in ("xhr", "fetch") and 200 <= res_status < 300:
                    try:
                        content_type = response_obj.headers.get("content-type", "")
                        if "json" in content_type.lower():
                            body = response_obj.json()
                            if isinstance(body, (dict, list)) and body:
                                api_responses.append({
                                    "url": res_url,
                                    "status": res_status,
                                    "data": body,
                                    "record_count": len(body) if isinstance(body, list) else 1,
                                })
                                logger.info(
                                    "[DynamicFetcher] Intercepted API: %s (%s records)",
                                    res_url,
                                    len(body) if isinstance(body, list) else "object",
                                )
                    except Exception:
                        pass  # Non-JSON or malformed responses

            page.on("response", _handle_response)

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=self._config.fetcher.timeout * 1000)
                # Wait for network idle / JS rendering
                page.wait_for_timeout(effective_wait * 1000)
                elapsed = time.time() - start

                html = page.content()
                status = 200

                # Sort API responses by record count (largest first)
                api_responses.sort(key=lambda r: r.get("record_count", 0), reverse=True)

                logger.info(
                    "[DynamicFetcher] GET %s -> %d (%.1fs, JS rendered, %d API responses intercepted)",
                    url, status, elapsed, len(api_responses),
                )

                # Build an httpx.Response-like wrapper
                response = httpx.Response(
                    status_code=status,
                    content=html.encode("utf-8"),
                    headers={"content-type": "text/html; charset=utf-8"},
                    request=httpx.Request("GET", url),
                )
                # Attach intercepted API data for auto-analysis
                response.api_responses = api_responses  # type: ignore
                return response

            except Exception as exc:
                elapsed = time.time() - start
                logger.error(
                    "[DynamicFetcher] GET %s -> %s (%.1fs)",
                    url, type(exc).__name__, elapsed,
                )
                raise FetchError(f"Dynamic fetch failed for {url}: {exc}") from exc

            finally:
                browser.close()
