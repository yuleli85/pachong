"""Configuration management module.

Loads configuration from YAML files, applies defaults for missing keys,
and supports environment variable overrides.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


# ── Default values ────────────────────────────────────────────────

_DEFAULT_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (iPad; CPU OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (compatible; MSIE 10.0; Windows NT 6.1; Trident/6.0)",
    "Mozilla/5.0 (Windows NT 10.0; ARM64; rv:125.0) Gecko/20100101 Firefox/125.0",
]


# ── Dataclass definitions ─────────────────────────────────────────

@dataclass
class FetcherConfig:
    timeout: int = 30
    max_retries: int = 3
    retry_backoff_base: float = 2.0
    user_agent_rotation: bool = True
    dynamic_wait_time: float = 3.0


@dataclass
class ComplianceConfig:
    request_delay_min: float = 1.0
    request_delay_max: float = 3.0
    forbidden_keywords: list[str] = field(default_factory=lambda: [
        "phone", "mobile", "id_card", "password", "email",
    ])
    allowed_domains: list[str] = field(default_factory=list)


@dataclass
class CleanerConfig:
    dedup_fields: list[str] = field(default_factory=lambda: ["url"])
    persist_dedup: bool = False
    persist_path: str = "dedup.db"


@dataclass
class SaverConfig:
    export_format: str = "xlsx"
    export_path: str = "output"
    log_level: str = "INFO"
    log_path: str = "logs/app.log"


@dataclass
class ParserConfig:
    default_selector: str = "css"


@dataclass
class AppConfig:
    fetcher: FetcherConfig = field(default_factory=FetcherConfig)
    compliance: ComplianceConfig = field(default_factory=ComplianceConfig)
    cleaner: CleanerConfig = field(default_factory=CleanerConfig)
    saver: SaverConfig = field(default_factory=SaverConfig)
    parser: ParserConfig = field(default_factory=ParserConfig)


# ── Helpers ───────────────────────────────────────────────────────

def _env(name: str, default: Any) -> Any:
    """Read an environment variable with fallback to default value."""
    val = os.environ.get(name)
    if val is None:
        return default
    if isinstance(default, bool):
        return val.lower() in ("true", "1", "yes")
    if isinstance(default, int):
        return int(val)
    if isinstance(default, float):
        return float(val)
    return val


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into *base*, returning a new dict."""
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def _apply_env_overrides(cfg_dict: dict) -> dict:
    """Apply environment variable overrides to the config dict.

    Supported env vars: FETCHER_TIMEOUT, FETCHER_MAX_RETRIES,
    FETCHER_RETRY_BACKOFF_BASE, FETCHER_USER_AGENT_ROTATION,
    COMPLIANCE_REQUEST_DELAY_MIN, COMPLIANCE_REQUEST_DELAY_MAX,
    SAVER_EXPORT_FORMAT, SAVER_EXPORT_PATH, SAVER_LOG_LEVEL, SAVER_LOG_PATH,
    PARSER_DEFAULT_SELECTOR.
    """
    mapping = {
        "FETCHER_TIMEOUT": ("fetcher", "timeout"),
        "FETCHER_MAX_RETRIES": ("fetcher", "max_retries"),
        "FETCHER_RETRY_BACKOFF_BASE": ("fetcher", "retry_backoff_base"),
        "FETCHER_USER_AGENT_ROTATION": ("fetcher", "user_agent_rotation"),
        "COMPLIANCE_REQUEST_DELAY_MIN": ("compliance", "request_delay_min"),
        "COMPLIANCE_REQUEST_DELAY_MAX": ("compliance", "request_delay_max"),
        "SAVER_EXPORT_FORMAT": ("saver", "export_format"),
        "SAVER_EXPORT_PATH": ("saver", "export_path"),
        "SAVER_LOG_LEVEL": ("saver", "log_level"),
        "SAVER_LOG_PATH": ("saver", "log_path"),
        "PARSER_DEFAULT_SELECTOR": ("parser", "default_selector"),
    }
    for env_name, (section, key) in mapping.items():
        if env_name in os.environ:
            cfg_dict.setdefault(section, {})[key] = _env(
                env_name, cfg_dict.get(section, {}).get(key)
            )
    return cfg_dict


def _dict_to_dataclass(cfg: dict) -> AppConfig:
    """Convert a plain dict to typed AppConfig dataclass."""
    return AppConfig(
        fetcher=FetcherConfig(**cfg.get("fetcher", {})),
        compliance=ComplianceConfig(**cfg.get("compliance", {})),
        cleaner=CleanerConfig(**cfg.get("cleaner", {})),
        saver=SaverConfig(**cfg.get("saver", {})),
        parser=ParserConfig(**cfg.get("parser", {})),
    )


# ── Public API ────────────────────────────────────────────────────

def load_config(path: str | Path | None = None) -> AppConfig:
    """Load configuration from YAML file and return an ``AppConfig`` instance.

    Args:
        path: Path to the YAML config file. Defaults to ``config.yaml``
              next to this module.

    Returns:
        Fully populated ``AppConfig`` dataclass with env overrides applied.
    """
    default_cfg = {
        "fetcher": {
            "timeout": 30,
            "max_retries": 3,
            "retry_backoff_base": 2.0,
            "user_agent_rotation": True,
            "dynamic_wait_time": 3.0,
        },
        "compliance": {
            "request_delay_min": 1.0,
            "request_delay_max": 3.0,
            "forbidden_keywords": ["phone", "mobile", "id_card", "password", "email"],
            "allowed_domains": [],
        },
        "cleaner": {
            "dedup_fields": ["url"],
            "persist_dedup": False,
            "persist_path": "dedup.db",
        },
        "saver": {
            "export_format": "xlsx",
            "export_path": "output",
            "log_level": "INFO",
            "log_path": "logs/app.log",
        },
        "parser": {
            "default_selector": "css",
        },
    }

    if path is None:
        path = Path(__file__).parent / "config.yaml"

    file_path = Path(path)
    if file_path.exists():
        with open(file_path, encoding="utf-8") as f:
            file_cfg = yaml.safe_load(f) or {}
        merged = _deep_merge(default_cfg, file_cfg)
    else:
        merged = default_cfg

    merged = _apply_env_overrides(merged)
    return _dict_to_dataclass(merged)
