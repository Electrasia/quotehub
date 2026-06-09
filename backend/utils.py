"""
backend/utils.py — Shared utility functions for QuoteHub.

This module contains common helper functions used across the backend.
Moving these here eliminates circular dependencies between main.py,
normalize.py, and ocr.py.

Functions:
    load_config: Read configuration from config.json
    save_config: Write configuration to config.json
    get_config_data: Read config with default values
    repair_json_quotes: Fix unescaped quotes in JSON strings
"""

import json
from pathlib import Path

# ─── Configuration Paths ─────────────────────────────────────────────────────
# CONFIG_PATH points to config.json in the parent directory (project root)
CONFIG_PATH = Path(__file__).parent.parent / "config.json"

# Default configuration values used when config.json is missing or incomplete
_CONFIG_DEFAULTS = {
    "ai_endpoint": "",
    "model": "",
    "timeout": 90,
    "max_retries": 2,
    "external_url": "",
    "popup_duration": 3,
    "session_max_age": 60,
    "idle_timeout_minutes": 15,
    "ocr_enabled": True,
    "ocr_fallback_to_llm": True,
    "extraction_mode": "local_first",  # llm_first | local_first | llm_only | local_only
}


def load_config():
    """Read configuration from config.json.

    Returns:
        dict: Configuration dictionary. If file not found, returns defaults.

    Example:
        >>> cfg = load_config()
        >>> print(cfg.get("ai_endpoint"))  # e.g., "http://localhost:1234/v1/..."
    """
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        return dict(_CONFIG_DEFAULTS)


def save_config(cfg):
    """Write configuration to config.json.

    Args:
        cfg (dict): Configuration dictionary to save.

    Example:
        >>> cfg = load_config()
        >>> cfg["ai_endpoint"] = "http://new-server:1234/v1/..."
        >>> save_config(cfg)
    """
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)


def get_config_data():
    """Read configuration with default values filled in.

    This function merges the saved config with default values,
    ensuring all required keys exist.

    Returns:
        dict: Complete configuration dictionary with defaults applied.

    Example:
        >>> cfg = get_config_data()
        >>> # cfg always has all keys, even if config.json was incomplete
        >>> print(cfg.get("timeout"))  # Always returns a value
    """
    cfg = load_config()
    for k, v in _CONFIG_DEFAULTS.items():
        cfg.setdefault(k, v)
    return cfg


def repair_json_quotes(raw):
    """Fix unescaped double quotes inside JSON string values.

    LLM responses sometimes contain unescaped quotes inside strings,
    which breaks JSON parsing. This function repairs them by detecting
    quote positions and escaping the ones that are inside strings.

    Args:
        raw (str): Raw JSON string that may have unescaped quotes.

    Returns:
        str: Repaired JSON string with proper escaping.

    Example:
        >>> bad_json = '{"name": "John "Johnny" Doe"}'
        >>> repair_json_quotes(bad_json)
        '{"name": "John \\"Johnny\\" Doe"}'
    """
    result = []
    in_string = False
    i = 0
    while i < len(raw):
        ch = raw[i]
        if ch == '"' and (i == 0 or raw[i-1] != '\\'):
            if not in_string:
                in_string = True
                result.append(ch)
            else:
                # Check if this quote ends the string (followed by:, }, ], etc.)
                rest = raw[i+1:i+20].lstrip()
                if rest and rest[0] in ':,}]\n':
                    in_string = False
                    result.append(ch)
                else:
                    # Quote is inside a string, needs escaping
                    result.append('\\"')
        else:
            result.append(ch)
        i += 1
    return ''.join(result)
