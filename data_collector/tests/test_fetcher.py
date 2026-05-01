"""Tests for the Fetcher module."""

import sys
import os
import time
from unittest.mock import MagicMock, patch

import httpx
import pytest

# Ensure parent dir is on the import path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import AppConfig, FetcherConfig
from fetcher import Fetcher, FetchError, ProxyError
from proxy_provider import ProxyProvider


@pytest.fixture
def config():
    """Default AppConfig with minimal retries for fast tests."""
    return AppConfig(fetcher=FetcherConfig(timeout=5, max_retries=2, retry_backoff_base=0.1))


@pytest.fixture
def fetcher(config):
    return Fetcher(config)


class MockResponse:
    def __init__(self, status_code=200, text="ok", json_data=None):
        self.status_code = status_code
        self._text = text
        self._json = json_data
        self.elapsed = MagicMock(total_seconds=lambda: 0.1)
        self.headers = {"content-type": "text/html"}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)

    def json(self):
        return self._json or {}


class TestFetcherSuccess:
    """Tests for successful request scenarios."""

    @patch("fetcher.httpx.Client")
    def test_get_success(self, mock_client, fetcher):
        mock_resp = MockResponse(status_code=200)
        mock_instance = MagicMock()
        mock_instance.request.return_value = mock_resp
        mock_client.return_value.__enter__ = MagicMock(return_value=mock_instance)
        mock_client.return_value.__exit__ = MagicMock(return_value=False)

        resp = fetcher.get("https://example.com")
        assert resp.status_code == 200

    @patch("fetcher.httpx.Client")
    def test_post_success(self, mock_client, fetcher):
        mock_resp = MockResponse(status_code=201)
        mock_instance = MagicMock()
        mock_instance.request.return_value = mock_resp
        mock_client.return_value.__enter__ = MagicMock(return_value=mock_instance)
        mock_client.return_value.__exit__ = MagicMock(return_value=False)

        resp = fetcher.post("https://example.com", json={"key": "value"})
        assert resp.status_code == 201

    @patch("fetcher.httpx.Client")
    def test_custom_headers_override(self, mock_client, config):
        fetcher = Fetcher(config)
        mock_resp = MockResponse()
        mock_instance = MagicMock()
        mock_instance.request.return_value = mock_resp
        mock_client.return_value.__enter__ = MagicMock(return_value=mock_instance)
        mock_client.return_value.__exit__ = MagicMock(return_value=False)

        fetcher.get("https://example.com", headers={"User-Agent": "MyBot/1.0"})
        call_kwargs = mock_instance.request.call_args.kwargs
        assert call_kwargs["headers"]["User-Agent"] == "MyBot/1.0"

    @patch("fetcher.httpx.Client")
    def test_fetch_api_returns_json(self, mock_client, fetcher):
        mock_resp = MockResponse(json_data={"status": "ok"})
        mock_instance = MagicMock()
        mock_instance.request.return_value = mock_resp
        mock_client.return_value.__enter__ = MagicMock(return_value=mock_instance)
        mock_client.return_value.__exit__ = MagicMock(return_value=False)

        result = fetcher.fetch_api("https://api.example.com/data")
        assert result == {"status": "ok"}


class TestFetcherRetry:
    """Tests for retry and failure scenarios."""

    @patch("fetcher.httpx.Client")
    def test_retry_on_500_then_success(self, mock_client, fetcher):
        """First two attempts fail with 500, third succeeds."""
        fail_resp = MagicMock()
        fail_resp.status_code = 500
        fail_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500", request=None, response=fail_resp,
        )
        success_resp = MockResponse(status_code=200)

        mock_instance = MagicMock()
        mock_instance.request.side_effect = [
            httpx.HTTPStatusError("500", request=None, response=fail_resp),
            httpx.HTTPStatusError("500", request=None, response=fail_resp),
            success_resp,
        ]
        mock_client.return_value.__enter__ = MagicMock(return_value=mock_instance)
        mock_client.return_value.__exit__ = MagicMock(return_value=False)

        resp = fetcher.get("https://example.com")
        assert resp.status_code == 200
        assert mock_instance.request.call_count == 3

    @patch("fetcher.httpx.Client")
    def test_all_retries_fail_raises_fetch_error(self, mock_client, fetcher):
        mock_instance = MagicMock()
        mock_instance.request.side_effect = httpx.ConnectError("refused")
        mock_client.return_value.__enter__ = MagicMock(return_value=mock_instance)
        mock_client.return_value.__exit__ = MagicMock(return_value=False)

        with pytest.raises(FetchError):
            fetcher.get("https://example.com")

    @patch("fetcher.httpx.Client")
    def test_timeout_triggers_retry(self, mock_client, fetcher):
        mock_instance = MagicMock()
        mock_instance.request.side_effect = httpx.TimeoutException("timed out")
        mock_client.return_value.__enter__ = MagicMock(return_value=mock_instance)
        mock_client.return_value.__exit__ = MagicMock(return_value=False)

        with pytest.raises(FetchError):
            fetcher.get("https://example.com")


class TestFetcherProxy:
    """Tests for proxy-related behaviour."""

    def test_set_proxy_provider(self, fetcher):
        provider = MagicMock(spec=ProxyProvider)
        fetcher.set_proxy_provider(provider)
        assert fetcher._proxy_provider is provider

    @patch("fetcher.httpx.Client")
    def test_uses_proxy_provider(self, mock_client, fetcher):
        provider = MagicMock(spec=ProxyProvider)
        provider.get_proxy.return_value = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}
        fetcher.set_proxy_provider(provider)

        mock_resp = MockResponse()
        mock_instance = MagicMock()
        mock_instance.request.return_value = mock_resp
        mock_client.return_value.__enter__ = MagicMock(return_value=mock_instance)
        mock_client.return_value.__exit__ = MagicMock(return_value=False)

        fetcher.get("https://example.com")
        provider.get_proxy.assert_called_once()

    @patch("fetcher.httpx.Client")
    def test_static_proxy_overrides_provider(self, mock_client, fetcher):
        provider = MagicMock(spec=ProxyProvider)
        fetcher.set_proxy_provider(provider)

        mock_resp = MockResponse()
        mock_instance = MagicMock()
        mock_instance.request.return_value = mock_resp
        mock_client.return_value.__enter__ = MagicMock(return_value=mock_instance)
        mock_client.return_value.__exit__ = MagicMock(return_value=False)

        static_proxy = {"http": "http://custom:8080", "https": "http://custom:8080"}
        fetcher.get("https://example.com", proxy=static_proxy)
        # Static proxy should be used, not the provider
        provider.get_proxy.assert_not_called()
