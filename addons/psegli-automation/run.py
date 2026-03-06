#!/usr/bin/env python3
"""PSEG Long Island Automation Addon — FastAPI Server.

Provides HTTP endpoints for the Home Assistant integration to obtain
authenticated cookies from mysmartenergy.psegliny.com.
"""

import asyncio
import json
import logging
import os
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


DEBUG_ENABLED = _load_debug_enabled()
LOG_LEVEL = logging.DEBUG if DEBUG_ENABLED else logging.INFO
logging.basicConfig(level=LOG_LEVEL)
logger = logging.getLogger(__name__)

app = FastAPI(title="PSEG Long Island Automation", version="2.5.1.3")

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
