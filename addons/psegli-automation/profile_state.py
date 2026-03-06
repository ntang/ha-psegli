#!/usr/bin/env python3
"""Browser profile state for PSEG add-on (Phase D).

Persists profile_created_at, profile_last_success_at, recent_captcha_count,
profile_size_bytes, and warmup_state for the /profile-status endpoint.
"""

import json
import logging
import os
import time
from typing import Any

_LOGGER = logging.getLogger(__name__)

# Persistent paths (add-on /data is writable in HA)
DATA_DIR = "/data"
PROFILE_STATE_PATH = os.path.join(DATA_DIR, "profile_state.json")
PROFILE_DIR_PERSISTENT = os.path.join(DATA_DIR, ".browser_profile")

# Warm-up states for profile-status
WARMUP_IDLE = "idle"
WARMUP_WARMING = "warming"
WARMUP_READY = "ready"
WARMUP_FAILED = "failed"


def _ensure_data_dir() -> bool:
    """Ensure /data exists (best-effort)."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        return True
    except OSError as e:
        _LOGGER.warning("Could not create data dir %s: %s", DATA_DIR, e)
        return False


def load_profile_state() -> dict[str, Any]:
    """Load profile state from disk. Returns defaults if missing or invalid."""
    default = {
        "profile_created_at": None,
        "profile_last_success_at": None,
        "recent_captcha_count": 0,
        "profile_size_bytes": None,
        "warmup_state": WARMUP_IDLE,
    }
    if not os.path.isfile(PROFILE_STATE_PATH):
        return default
    try:
        with open(PROFILE_STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {**default, **data}
    except (OSError, json.JSONDecodeError) as e:
        _LOGGER.warning("Could not load profile state from %s: %s", PROFILE_STATE_PATH, e)
        return default


def save_profile_state(state: dict[str, Any]) -> None:
    """Persist profile state. Best-effort; logs on failure."""
    if not _ensure_data_dir():
        return
    try:
        with open(PROFILE_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except OSError as e:
        _LOGGER.warning("Could not save profile state to %s: %s", PROFILE_STATE_PATH, e)


def record_profile_created() -> None:
    """Call after creating or rotating to a fresh profile."""
    state = load_profile_state()
    state["profile_created_at"] = time.time()
    state["warmup_state"] = WARMUP_IDLE
    state["recent_captcha_count"] = 0
    save_profile_state(state)


def record_login_success() -> None:
    """Call after a successful login (cookies obtained)."""
    state = load_profile_state()
    state["profile_last_success_at"] = time.time()
    state["warmup_state"] = WARMUP_READY
    save_profile_state(state)


def record_captcha() -> None:
    """Call when CAPTCHA was required."""
    state = load_profile_state()
    state["recent_captcha_count"] = state.get("recent_captcha_count", 0) + 1
    save_profile_state(state)


def record_profile_failed() -> None:
    """Call when profile is corrupted or last run failed."""
    state = load_profile_state()
    state["warmup_state"] = WARMUP_FAILED
    save_profile_state(state)


def set_warmup_state(value: str) -> None:
    """Set warmup_state (idle|warming|ready|failed)."""
    state = load_profile_state()
    state["warmup_state"] = value
    save_profile_state(state)


def get_profile_size_bytes(profile_dir: str) -> int | None:
    """Return total size in bytes of profile directory, or None if not accessible."""
    if not os.path.isdir(profile_dir):
        return None
    try:
        total = 0
        for _root, _dirs, files in os.walk(profile_dir):
            for f in files:
                path = os.path.join(_root, f)
                try:
                    total += os.path.getsize(path)
                except OSError:
                    pass
        return total
    except OSError:
        return None


def get_profile_status(profile_dir: str) -> dict[str, Any]:
    """Build the /profile-status response payload."""
    state = load_profile_state()
    size = get_profile_size_bytes(profile_dir)
    return {
        "profile_created_at": state.get("profile_created_at"),
        "profile_last_success_at": state.get("profile_last_success_at"),
        "recent_captcha_count": state.get("recent_captcha_count", 0),
        "profile_size_bytes": size if size is not None else state.get("profile_size_bytes"),
        "warmup_state": state.get("warmup_state", WARMUP_IDLE),
    }
