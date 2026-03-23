#!/usr/bin/env python3
"""PSEG Long Island Automation Addon — FastAPI Server.

Provides HTTP endpoints for the Home Assistant integration to obtain
authenticated cookies from mysmartenergy.psegliny.com.
"""

import asyncio
import json
import logging
import os
import time
from typing import Optional

import uvicorn
from fastapi import FastAPI, Form
from pydantic import BaseModel

from artifacts import list_login_failure_artifacts, prune_login_failure_artifacts
from auto_login import (
    CAPTCHA_REQUIRED_SENTINEL,
    FreshCookieResult,
    get_fresh_cookies,
    get_effective_profile_dir,
)
from profile_state import get_profile_status

# Set HEADED=1 to run browser in headed mode (visible) for debugging
HEADED = os.environ.get("HEADED", "").lower() in ("1", "true", "yes")


def _load_debug_enabled() -> bool:
    """Load debug toggle from env or addon options file."""
    env_value = os.environ.get("ADDON_DEBUG")
    if env_value is not None:
        return env_value.strip().lower() in ("1", "true", "yes", "on")

    options_path = "/data/options.json"
    try:
        with open(options_path, "r", encoding="utf-8") as f:
            options = json.load(f)
        return bool(options.get("debug", False))
    except FileNotFoundError:
        return False
    except Exception:
        # Avoid crashing startup due to malformed options.json.
        return False


DEBUG_STATE_PATH = "/data/debug_state.json"


def _load_auto_disable_hours() -> int:
    """Load debug_auto_disable_hours from env or addon options file."""
    env_value = os.environ.get("DEBUG_AUTO_DISABLE_HOURS")
    if env_value is not None:
        try:
            return max(0, int(env_value))
        except ValueError:
            return 0

    options_path = "/data/options.json"
    try:
        with open(options_path, "r", encoding="utf-8") as f:
            options = json.load(f)
        return max(0, int(options.get("debug_auto_disable_hours", 0)))
    except (FileNotFoundError, ValueError, TypeError):
        return 0


def _load_debug_state() -> dict:
    """Load persisted debug state from /data."""
    try:
        with open(DEBUG_STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {
            "debug_enabled": False,
            "debug_enabled_at": None,
            "auto_disable_hours": 0,
        }


def _save_debug_state(state: dict) -> None:
    """Persist debug state to /data."""
    try:
        with open(DEBUG_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except OSError:
        pass  # Non-fatal: /data may not exist in dev/test environments


def _check_auto_disable() -> bool:
    """Check if debug should be auto-disabled based on elapsed time.

    Returns True if auto-disable fired (debug was turned off).
    """
    state = _load_debug_state()
    if not state.get("debug_enabled"):
        return False

    auto_hours = state.get("auto_disable_hours", 0)
    if auto_hours <= 0:
        return False

    enabled_at = state.get("debug_enabled_at")
    if enabled_at is None:
        return False

    elapsed_hours = (time.time() - enabled_at) / 3600
    if elapsed_hours >= auto_hours:
        # Auto-disable: flip state and lower log level at runtime
        state["debug_enabled"] = False
        _save_debug_state(state)

        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        for handler in root_logger.handlers:
            handler.setLevel(logging.INFO)
        logging.getLogger(__name__).info(
            "Debug auto-disabled after %.1f hours (threshold: %d hours)",
            elapsed_hours,
            auto_hours,
        )
        return True

    return False


def _apply_debug_startup_state(
    debug_from_config: bool, auto_disable_hours: int
) -> bool:
    """Reconcile config debug flag with persisted auto-disable state.

    Returns the effective debug_enabled value after reconciliation.
    """
    if debug_from_config:
        existing = _load_debug_state()
        if not existing.get("debug_enabled") and existing.get("debug_enabled_at") is not None:
            # Auto-disable previously fired (state=False but timestamp exists).
            # Honor the persisted decision: downgrade to INFO without re-arming.
            root = logging.getLogger()
            root.setLevel(logging.INFO)
            for handler in root.handlers:
                handler.setLevel(logging.INFO)
            logging.getLogger(__name__).info(
                "Debug was auto-disabled in a prior run; staying at INFO "
                "(set debug: false then debug: true to re-arm)"
            )
            return False
        elif not existing.get("debug_enabled"):
            # First time debug is turned on — record the timestamp
            _save_debug_state({
                "debug_enabled": True,
                "debug_enabled_at": time.time(),
                "auto_disable_hours": auto_disable_hours,
            })
        else:
            # Debug was already on — update auto_disable_hours if changed
            if existing.get("auto_disable_hours") != auto_disable_hours:
                existing["auto_disable_hours"] = auto_disable_hours
                _save_debug_state(existing)
        # Check if auto-disable should fire immediately (e.g., after restart)
        _check_auto_disable()
        return True
    else:
        # Debug is off — clear persisted state entirely so next enable is a fresh cycle
        existing = _load_debug_state()
        if existing.get("debug_enabled") or existing.get("debug_enabled_at") is not None:
            _save_debug_state({
                "debug_enabled": False,
                "debug_enabled_at": None,
                "auto_disable_hours": auto_disable_hours,
            })
        return False


DEBUG_ENABLED = _load_debug_enabled()
_AUTO_DISABLE_HOURS = _load_auto_disable_hours()
LOG_LEVEL = logging.DEBUG if DEBUG_ENABLED else logging.INFO
logging.basicConfig(level=LOG_LEVEL)
logger = logging.getLogger(__name__)

# Reconcile config toggle with persisted auto-disable state
DEBUG_ENABLED = _apply_debug_startup_state(DEBUG_ENABLED, _AUTO_DISABLE_HOURS)

app = FastAPI(title="PSEG Long Island Automation", version="2.5.2.1")

# Prevent concurrent login attempts (Playwright can only run one at a time)
_login_lock = asyncio.Lock()

if HEADED:
    logger.info("HEADED mode enabled — browser will be visible")
if DEBUG_ENABLED:
    logger.info("Addon debug logging enabled")
else:
    logger.info("Addon debug logging disabled")


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    success: bool
    cookies: Optional[str] = None
    error: Optional[str] = None
    captcha_required: Optional[bool] = None
    category: Optional[str] = None
    subreason: Optional[str] = None


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "psegli-automation"}


@app.on_event("startup")
async def startup_maintenance():
    """Apply retention pruning on startup."""
    await asyncio.to_thread(prune_login_failure_artifacts)
    # Start periodic debug auto-disable checker
    if _AUTO_DISABLE_HOURS > 0 and DEBUG_ENABLED:
        asyncio.create_task(_periodic_auto_disable_check())


async def _periodic_auto_disable_check():
    """Background task: check auto-disable every 5 minutes."""
    while True:
        await asyncio.sleep(300)  # 5-minute interval
        try:
            fired = await asyncio.to_thread(_check_auto_disable)
            if fired:
                logger.info("Debug auto-disable check completed — debug logging disabled")
                break  # No need to keep checking once disabled
        except Exception as e:
            logger.warning("Debug auto-disable check error: %s", e)


@app.get("/debug-status")
async def debug_status():
    """Return current debug lifecycle state."""
    state = _load_debug_state()
    enabled_at = state.get("debug_enabled_at")
    auto_hours = state.get("auto_disable_hours", 0)

    auto_disable_at = None
    if enabled_at and auto_hours and auto_hours > 0:
        auto_disable_at = enabled_at + (auto_hours * 3600)

    return {
        "debug_enabled": state.get("debug_enabled", False),
        "auto_disable_hours": auto_hours,
        "debug_enabled_at": enabled_at,
        "auto_disable_at": auto_disable_at,
    }


@app.get("/profile-status")
async def profile_status():
    """Profile status for Phase D: profile_created_at, last_success, captcha count, size, warmup_state."""
    profile_dir = get_effective_profile_dir()
    return await asyncio.to_thread(get_profile_status, profile_dir)


@app.get("/artifacts/login-failures")
async def login_failure_artifacts(limit: int = 10):
    """Metadata-only listing of login-failure artifacts."""
    safe_limit = max(1, min(limit, 100))
    return await asyncio.to_thread(list_login_failure_artifacts, safe_limit)


@app.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest):
    """Login to PSEG mysmartenergy and return session cookies."""
    async with _login_lock:
        try:
            logger.info("Login attempt for user: %s", request.username)

            raw_result = await get_fresh_cookies(
                username=request.username,
                password=request.password,
                headless=not HEADED,
                include_failure_details=True,
            )

            if isinstance(raw_result, FreshCookieResult):
                result = raw_result
            elif isinstance(raw_result, str):
                if raw_result == CAPTCHA_REQUIRED_SENTINEL:
                    result = FreshCookieResult(
                        cookies=None,
                        category="captcha_required",
                        captcha_required=True,
                        error=(
                            "reCAPTCHA challenge triggered. "
                            "Try again — it usually passes after a few attempts "
                            "with the persistent browser profile."
                        ),
                    )
                else:
                    result = FreshCookieResult(cookies=raw_result)
            elif isinstance(raw_result, dict):
                result = FreshCookieResult(
                    cookies=raw_result.get("cookies"),
                    category=raw_result.get("category"),
                    subreason=raw_result.get("subreason"),
                    error=raw_result.get("error"),
                    captcha_required=bool(raw_result.get("captcha_required")),
                )
            else:
                result = FreshCookieResult(
                    cookies=None,
                    category="unknown_runtime_error",
                    error="Login failed",
                )

            if result.captcha_required:
                logger.warning("CAPTCHA required — manual intervention needed")
                return LoginResponse(
                    success=False,
                    captcha_required=True,
                    error=result.error,
                    category=result.category,
                    subreason=result.subreason,
                )

            if result.cookies:
                logger.info("Login successful, cookies obtained")
                return LoginResponse(success=True, cookies=result.cookies)

            logger.warning(
                "Login failed, no cookies returned (category=%s subreason=%s)",
                result.category,
                result.subreason,
            )
            return LoginResponse(
                success=False,
                error=result.error or "Login failed",
                category=result.category,
                subreason=result.subreason,
            )

        except Exception as e:
            logger.error("Login error: %s", e)
            return LoginResponse(success=False, error=str(e))


@app.post("/login-form", response_model=LoginResponse)
async def login_form(
    username: str = Form(...),
    password: str = Form(...),
):
    """Login endpoint that accepts form data."""
    return await login(LoginRequest(username=username, password=password))


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        workers=1,
        log_level="debug" if DEBUG_ENABLED else "info",
    )
