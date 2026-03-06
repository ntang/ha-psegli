#!/usr/bin/env python3
"""Automated login for PSEG Long Island using the automation addon."""

import asyncio
import logging
import random
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

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

# Common HA add-on DNS names seen across environments.
_FALLBACK_ADDON_URLS = (
    "http://84ee8c30-psegli-automation:8000",
    "http://84ee8c30_psegli-automation:8000",
    "http://psegli-automation:8000",
)


def _normalize_addon_url(addon_url: Optional[str]) -> str:
    """Normalize configured addon URL with fallback + no trailing slash."""
    raw = addon_url or DEFAULT_ADDON_URL
    return raw.rstrip("/")


def _build_addon_url_candidates(addon_url: Optional[str]) -> list[str]:
    """Build prioritized addon URL candidates for resilient probing."""
    primary = _normalize_addon_url(addon_url)
    default = _normalize_addon_url(DEFAULT_ADDON_URL)
    host = (urlparse(primary).hostname or "").lower()
    is_local = host in {"localhost", "127.0.0.1", "::1"}

    candidates: list[str] = [primary]

    # If configured URL is local, prefer container DNS names next.
    if is_local:
        candidates.extend(_FALLBACK_ADDON_URLS)
    else:
        # Keep default as a fallback when primary is remote/custom.
        candidates.append(default)
        candidates.extend(_FALLBACK_ADDON_URLS)

    # Preserve order while removing duplicates.
    unique: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = _normalize_addon_url(candidate)
        if normalized not in seen:
            seen.add(normalized)
            unique.append(normalized)
    return unique


def _candidate_for_attempt(candidates: list[str], attempt: int) -> str:
    """Select candidate for 1-based attempt index."""
    if not candidates:
        return _normalize_addon_url(None)
    if len(candidates) == 1:
        return candidates[0]
    # With multiple candidates, probe a new one per retry until exhausted.
    idx = min(attempt - 1, len(candidates) - 1)
    return candidates[idx]


async def check_addon_health(addon_url: Optional[str] = None) -> bool:
    """Check if the addon is available and healthy.

    Best-effort fast-fail — callers should still handle errors from
    subsequent addon calls (the addon could go down between the health
    check and the actual request).
    """
    candidates = _build_addon_url_candidates(addon_url)
    logger.info("Addon health check candidates: %s", ", ".join(candidates))
    timeout = aiohttp.ClientTimeout(total=5)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for idx, base_url in enumerate(candidates, start=1):
            health_url = f"{base_url}/health"
            logger.info(
                "Addon health check attempt %d/%d: %s",
                idx,
                len(candidates),
                health_url,
            )
            try:
                async with session.get(health_url) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        if result.get("status") == "healthy":
                            logger.info("Addon health check passed: %s", health_url)
                            return True
                    logger.warning(
                        "Addon health check failed: url=%s status=%s",
                        health_url,
                        resp.status,
                    )
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.warning(
                    "Addon health check transport failure: url=%s error=%s (%s)",
                    health_url,
                    e,
                    type(e).__name__,
                )
    return False


async def _attempt_login(
    session: aiohttp.ClientSession,
    login_data: dict,
    addon_url: Optional[str] = None,
) -> LoginResult:
    """Single login attempt against the addon /login endpoint.

    Returns:
        LoginResult with cookies on success, or a failure category.

    Raises:
        aiohttp.ClientError or asyncio.TimeoutError on transport failures
        and 5xx server errors (these are retryable by the caller).
    """
    base_url = _normalize_addon_url(addon_url)
    login_url = f"{base_url}/login"
    logger.info("Addon login request: %s", login_url)
    async with session.post(login_url, json=login_data) as resp:
        logger.info("Addon login response: url=%s status=%s", login_url, resp.status)
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
                "Addon login failed: url=%s error=%s",
                login_url,
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
            logger.error(
                "Addon request failed: url=%s status=%s",
                login_url,
                resp.status,
            )
            return LoginResult(category=CATEGORY_UNKNOWN_ERROR)


async def get_fresh_cookies(
    username: str,
    password: str,
    addon_url: Optional[str] = None,
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
    primary_url = _normalize_addon_url(addon_url)
    candidates = _build_addon_url_candidates(addon_url)
    logger.info(
        "Requesting fresh cookies from addon: primary_url=%s candidates=%s retries=%d timeout=%ss",
        primary_url,
        ", ".join(candidates),
        _MAX_LOGIN_RETRIES,
        120,
    )

    timeout = aiohttp.ClientTimeout(total=120)
    login_data = {
        "username": username,
        "password": password,
    }

    last_transport_error: Optional[Exception] = None
    attempted_urls: list[str] = []

    for attempt in range(1, _MAX_LOGIN_RETRIES + 1):
        attempt_url = _candidate_for_attempt(candidates, attempt)
        attempted_urls.append(attempt_url)
        try:
            logger.info(
                "Addon login attempt %d/%d via %s/login",
                attempt,
                _MAX_LOGIN_RETRIES,
                attempt_url,
            )
            async with aiohttp.ClientSession(timeout=timeout) as session:
                result = await _attempt_login(
                    session,
                    login_data,
                    addon_url=attempt_url,
                )
            # Any non-exception return is a functional response — don't retry.
            return result

        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            last_transport_error = e
            if attempt < _MAX_LOGIN_RETRIES:
                delay = _RETRY_BASE_DELAY * attempt + random.uniform(0, _RETRY_MAX_JITTER)
                next_url = _candidate_for_attempt(candidates, attempt + 1)
                if next_url != attempt_url:
                    logger.warning(
                        "Addon login transport failure (attempt %d/%d, url=%s/login): %s (%s) — switching to %s/login in %.1fs",
                        attempt,
                        _MAX_LOGIN_RETRIES,
                        attempt_url,
                        e,
                        type(e).__name__,
                        next_url,
                        delay,
                    )
                else:
                    logger.warning(
                        "Addon login transport failure (attempt %d/%d, url=%s/login): %s (%s) — retrying in %.1fs",
                        attempt,
                        _MAX_LOGIN_RETRIES,
                        attempt_url,
                        e,
                        type(e).__name__,
                        delay,
                    )
                await asyncio.sleep(delay)
            else:
                logger.error(
                    "Addon login transport failure (attempt %d/%d, url=%s/login): %s (%s) — no more retries",
                    attempt,
                    _MAX_LOGIN_RETRIES,
                    attempt_url,
                    e,
                    type(e).__name__,
                )

        except Exception as e:
            logger.exception(
                "Unexpected error getting cookies from addon url=%s: %s (%s)",
                attempt_url,
                e,
                type(e).__name__,
            )
            return LoginResult(category=CATEGORY_UNKNOWN_ERROR)

    # All retries exhausted due to transport failures
    logger.error(
        "Failed to connect to addon after %d attempts (tried=%s): %s (%s)",
        _MAX_LOGIN_RETRIES,
        ", ".join(dict.fromkeys(attempted_urls)),
        last_transport_error,
        type(last_transport_error).__name__ if last_transport_error else "Unknown",
    )
    return LoginResult(category=CATEGORY_ADDON_DISCONNECT)
