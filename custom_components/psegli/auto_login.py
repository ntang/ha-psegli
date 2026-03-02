#!/usr/bin/env python3
"""Automated login for PSEG Long Island using the automation addon."""

import asyncio
import logging
import random
from dataclasses import dataclass
from typing import Optional

import aiohttp

from .const import DEFAULT_ADDON_URL

logger = logging.getLogger(__name__)

# Sentinel value returned by the addon's get_cookies() when reCAPTCHA is triggered.
# Must match the string returned by PSEGAutoLogin.get_cookies() in the addon's
# auto_login.py (which converts LoginResult.CAPTCHA_REQUIRED to this string).
CAPTCHA_REQUIRED = "CAPTCHA_REQUIRED"

# Failure categories for diagnostics (Phase 3.2).
CATEGORY_ADDON_UNREACHABLE = "addon_unreachable"
CATEGORY_ADDON_DISCONNECT = "addon_disconnect"
CATEGORY_CAPTCHA_REQUIRED = "captcha_required"
CATEGORY_INVALID_CREDENTIALS = "invalid_credentials"
CATEGORY_UNKNOWN_ERROR = "unknown_runtime_error"


@dataclass
class LoginResult:
    """Result from get_fresh_cookies with failure classification."""

    cookies: Optional[str] = None
    category: Optional[str] = None  # one of CATEGORY_* constants on failure

# Retry configuration for transport failures (connection error, timeout, disconnect).
# Terminal responses (captcha_required, invalid credentials) are never retried.
_MAX_LOGIN_RETRIES = 3
_RETRY_BASE_DELAY = 2.0  # seconds
_RETRY_MAX_JITTER = 2.0  # seconds


async def check_addon_health() -> bool:
    """Check if the addon is available and healthy.

    Best-effort fast-fail — callers should still handle errors from
    subsequent addon calls (the addon could go down between the health
    check and the actual request).
    """
    try:
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{DEFAULT_ADDON_URL}/health") as resp:
                if resp.status == 200:
                    result = await resp.json()
                    if result.get("status") == "healthy":
                        logger.debug("Addon is healthy and available")
                        return True
                logger.debug("Addon health check failed: status=%s", resp.status)
                return False
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        logger.debug("Addon health check failed: %s", e)
        return False


async def _attempt_login(
    session: aiohttp.ClientSession,
    login_data: dict,
) -> LoginResult:
    """Single login attempt against the addon /login endpoint.

    Returns:
        LoginResult with cookies on success, or a failure category.

    Raises:
        aiohttp.ClientError or asyncio.TimeoutError on transport failures
        and 5xx server errors (these are retryable by the caller).
    """
    async with session.post(
        f"{DEFAULT_ADDON_URL}/login",
        json=login_data,
    ) as resp:
        if resp.status == 200:
            result = await resp.json()
            if result.get("success") and result.get("cookies"):
                logger.debug("Successfully obtained cookies from addon")
                return LoginResult(cookies=result["cookies"])
            if result.get("captcha_required"):
                logger.info(
                    "reCAPTCHA challenge triggered — retry usually resolves it"
                )
                return LoginResult(category=CATEGORY_CAPTCHA_REQUIRED)
            logger.error(
                "Addon login failed: %s",
                result.get("error", "Unknown error"),
            )
            return LoginResult(category=CATEGORY_INVALID_CREDENTIALS)
        elif resp.status >= 500:
            # Server errors are transient — raise so the retry loop catches it.
            raise aiohttp.ClientResponseError(
                resp.request_info,
                resp.history,
                status=resp.status,
                message=f"Server error {resp.status}",
            )
        else:
            # 4xx and other client errors are terminal
            logger.error("Addon request failed with status %s", resp.status)
            return LoginResult(category=CATEGORY_UNKNOWN_ERROR)


async def get_fresh_cookies(
    username: str,
    password: str,
) -> LoginResult:
    """Get fresh cookies using the automation addon.

    Transport failures (connection error, timeout, server disconnected, 5xx)
    are retried up to _MAX_LOGIN_RETRIES times with jittered backoff. Terminal
    functional responses (captcha_required, invalid credentials, 4xx) are
    returned immediately without retry.

    Note: No internal health check gate — callers that want fast-fail
    (e.g. scheduled refresh, manual refresh) already call
    check_addon_health() externally. Removing the gate here ensures
    transient /health failures don't bypass the retry loop.

    Returns:
        LoginResult with cookies on success, or a failure category.
    """
    logger.debug("Requesting fresh cookies from PSEG automation addon...")

    timeout = aiohttp.ClientTimeout(total=120)
    login_data = {
        "username": username,
        "password": password,
    }

    last_transport_error: Optional[Exception] = None

    for attempt in range(1, _MAX_LOGIN_RETRIES + 1):
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                result = await _attempt_login(session, login_data)
            # Any non-exception return is a functional response — don't retry.
            return result

        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            last_transport_error = e
            if attempt < _MAX_LOGIN_RETRIES:
                delay = _RETRY_BASE_DELAY * attempt + random.uniform(0, _RETRY_MAX_JITTER)
                logger.warning(
                    "Addon login transport failure (attempt %d/%d): %s — retrying in %.1fs",
                    attempt, _MAX_LOGIN_RETRIES, e, delay,
                )
                await asyncio.sleep(delay)
            else:
                logger.error(
                    "Addon login transport failure (attempt %d/%d): %s — no more retries",
                    attempt, _MAX_LOGIN_RETRIES, e,
                )

        except Exception:
            logger.exception("Unexpected error getting cookies from addon")
            return LoginResult(category=CATEGORY_UNKNOWN_ERROR)

    # All retries exhausted due to transport failures
    logger.error("Failed to connect to addon after %d attempts: %s", _MAX_LOGIN_RETRIES, last_transport_error)
    return LoginResult(category=CATEGORY_ADDON_DISCONNECT)
