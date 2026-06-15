"""
backend/utils.py — Shared utility functions for QuoteHub.

Functions:
    load_config: Read configuration from config.json
    save_config: Write configuration to config.json
    get_config_data: Read config with default values
    normalize_date: Convert various date formats to YYYY-MM-DD
"""

import json
from pathlib import Path
from datetime import datetime

# ─── Configuration Paths ─────────────────────────────────────
CONFIG_PATH = Path(__file__).parent.parent / "config.json"

_CONFIG_DEFAULTS = {
    "ai_endpoint": "",
    "model": "",
    "timeout": 120,
    "max_retries": 2,
    "external_url": "",
    "extraction_enabled": True,
    "popup_duration": 3,
    "session_max_age": 14 * 24 * 60 * 60,
    "idle_timeout_minutes": 15,
    "ocr_enabled": True,
    "ocr_fallback_to_llm": True,
    "max_upload_size_mb": 5,
}


def load_config():
    """Read configuration from config.json."""
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        return dict(_CONFIG_DEFAULTS)


def save_config(cfg):
    """Write configuration to config.json, merging with existing."""
    existing = load_config()
    merged = {**existing, **cfg}
    with open(CONFIG_PATH, "w") as f:
        json.dump(merged, f, indent=2)


def get_config_data():
    """Read configuration with defaults filled in."""
    cfg = load_config()
    for k, v in _CONFIG_DEFAULTS.items():
        cfg.setdefault(k, v)
    return cfg


# ─── Date Parsing ────────────────────────────────────────────

_DATE_FORMATS = [
    "%Y-%m-%d",           # 2019-06-20
    "%d-%m-%Y",           # 20-06-2019
    "%d/%m/%Y",           # 20/06/2019
    "%Y/%m/%d",           # 2019/06/20
    "%d-%b-%Y",           # 20-Jun-2019
    "%d %b %Y",           # 20 Jun 2019
    "%B %d, %Y",          # June 20, 2019
    "%d %B %Y",           # 20 June 2019
    "%m/%d/%Y",           # 06/20/2019 (US format)
]


def normalize_date(raw_date: str) -> str:
    """Convert any common date format to YYYY-MM-DD.

    Tries multiple format patterns with dayfirst preference
    (DD/MM/YYYY is more common than MM/DD/YYYY globally).

    Args:
        raw_date: Date string in any common format

    Returns:
        YYYY-MM-DD string, or empty string if unparseable
    """
    if not raw_date or not raw_date.strip():
        return ""

    raw = raw_date.strip().rstrip(",")

    # First try explicit formats
    for fmt in _DATE_FORMATS:
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.strftime("%Y-%m-%d")
        except (ValueError, IndexError):
            continue

    # Try dateutil-style parsing with dayfirst preference
    # (handles ambiguous cases like "06/05/2019" as 6 May 2019)
    try:
        parts = raw.replace("/", "-").split("-")
        if len(parts) == 3:
            # Try day-first
            d, m, y = int(parts[0]), int(parts[1]), int(parts[2])
            if 1 <= d <= 31 and 1 <= m <= 12:
                return f"{y:04d}-{m:02d}-{d:02d}"
            # Try month-first (US format)
            m2, d2 = parts[0], parts[1]
            if 1 <= int(m2) <= 12 and 1 <= int(d2) <= 31:
                return f"{y:04d}-{int(m2):02d}-{int(d2):02d}"
    except (ValueError, IndexError):
        pass

    return raw_date  # return as-is if can't parse
