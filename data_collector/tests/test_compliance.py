"""Tests for the Compliance module."""

import sys
import os
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from compliance import Compliance, ComplianceError


# ── Rate limiting ────────────────────────────────────────────────────

class TestRateLimit:
    def test_sleeps_at_least_min(self):
        c = Compliance(request_delay_min=0.05, request_delay_max=0.1)
        start = time.time()
        c.rate_limit()
        elapsed = time.time() - start
        assert elapsed >= 0.04  # small tolerance

    def test_set_delay_range(self):
        c = Compliance(request_delay_min=1.0, request_delay_max=3.0)
        c.set_delay_range(0.01, 0.02)
        start = time.time()
        c.rate_limit()
        elapsed = time.time() - start
        assert elapsed < 0.05  # should be very short now


# ── Domain whitelist ─────────────────────────────────────────────────

class TestCheckDomain:
    def test_allowed_domain_passes(self):
        c = Compliance(allowed_domains=["example.com"])
        assert c.check_domain("https://example.com/page") is True

    def test_subdomain_allowed(self):
        c = Compliance(allowed_domains=["example.com"])
        assert c.check_domain("https://sub.example.com/page") is True

    def test_disallowed_domain_raises(self):
        c = Compliance(allowed_domains=["example.com"])
        with pytest.raises(ComplianceError):
            c.check_domain("https://evil.com/page")

    def test_empty_whitelist_allows_all(self):
        c = Compliance(allowed_domains=[])
        assert c.check_domain("https://anything.com") is True


# ── Sensitive field filtering ────────────────────────────────────────

class TestFilterFields:
    def test_removes_matching_field(self):
        c = Compliance(forbidden_keywords=["phone"])
        data = [{"name": "Alice", "user_phone": "12345"}]
        result = c.filter_fields(data)
        assert "user_phone" not in result[0]
        assert result[0]["name"] == "Alice"

    def test_case_insensitive_match(self):
        c = Compliance(forbidden_keywords=["Phone"])
        data = [{"user_PHONE": "123"}]
        result = c.filter_fields(data)
        assert "user_PHONE" not in result[0]

    def test_no_keywords_means_no_filtering(self):
        c = Compliance(forbidden_keywords=[])
        data = [{"phone": "123", "email": "a@b.c"}]
        result = c.filter_fields(data)
        assert result == data

    def test_partial_key_match(self):
        c = Compliance(forbidden_keywords=["mobile"])
        data = [{"mobile_number": "13800138000"}]
        result = c.filter_fields(data)
        assert "mobile_number" not in result[0]


# ── Response validation ──────────────────────────────────────────────

class TestValidate:
    def test_detects_id_card_pattern(self):
        c = Compliance()
        records = [{"info": "身份证号: 110101199001011234"}]
        matched = c.validate(records)
        assert "id_card" in matched

    def test_detects_phone_pattern(self):
        c = Compliance()
        records = [{"contact": "手机号: 13812345678"}]
        matched = c.validate(records)
        assert "phone" in matched

    def test_detects_email_pattern(self):
        c = Compliance()
        records = [{"email": "user@example.com"}]
        matched = c.validate(records)
        assert "email" in matched

    def test_no_sensitive_data_returns_empty(self):
        c = Compliance()
        records = [{"title": "Normal article"}]
        matched = c.validate(records)
        assert matched == []
