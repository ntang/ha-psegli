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

from auto_login import CAPTCHA_REQUIRED_SENTINEL, get_fresh_cookies, get_effective_profile_dir
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

app = FastAPI(title="PSEG Long Island Automation", version="2.5.1.1")

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


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "psegli-automation"}


@app.get("/profile-status")
async def profile_status():
    """Profile status for Phase D: profile_created_at, last_success, captcha count, size, warmup_state."""
    profile_dir = get_effective_profile_dir()
    return await asyncio.to_thread(get_profile_status, profile_dir)


@app.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest):
    """Login to PSEG mysmartenergy and return session cookies."""
    async with _login_lock:
        try:
            logger.info("Login attempt for user: %s", request.username)

            result = await get_fresh_cookies(
                username=request.username,
                password=request.password,
                headless=not HEADED,
            )

            if result == CAPTCHA_REQUIRED_SENTINEL:
                logger.warning("CAPTCHA required — manual intervention needed")
                return LoginResponse(
                    success=False,
                    captcha_required=True,
                    error=(
                        "reCAPTCHA challenge triggered. "
                        "Try again — it usually passes after a few attempts "
                        "with the persistent browser profile."
                    ),
                )

            if result:
                logger.info("Login successful, cookies obtained")
                return LoginResponse(success=True, cookies=result)

            logger.warning("Login failed, no cookies returned")
            return LoginResponse(success=False, error="Login failed")

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
