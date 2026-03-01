#!/usr/bin/env python3
"""Automated login for PSEG Long Island using the automation addon."""

import logging
from typing import Optional

import aiohttp

from .const import DEFAULT_ADDON_URL

logger = logging.getLogger(__name__)

# Sentinel for CAPTCHA required — reCAPTCHA challenge triggered, user should retry
CAPTCHA_REQUIRED = "CAPTCHA_REQUIRED"


async def check_addon_health() -> bool:
    """Check if the addon is available and healthy."""
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
    except Exception as e:
        logger.debug("Addon health check failed: %s", e)
        return False


async def get_fresh_cookies(
    username: str,
    password: str,
) -> Optional[str]:
    """Get fresh cookies using the automation addon.

    Returns:
        Cookie string on success, CAPTCHA_REQUIRED when reCAPTCHA challenge
        is triggered (retry usually resolves it), or None on failure.
    """
    try:
        logger.debug("Requesting fresh cookies from PSEG automation addon...")

        if not await check_addon_health():
            logger.warning("Addon not available or unhealthy, cannot get fresh cookies")
            return None

        timeout = aiohttp.ClientTimeout(total=120)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            login_data = {
                "username": username,
                "password": password,
            }

            async with session.post(
                f"{DEFAULT_ADDON_URL}/login",
                json=login_data,
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    if result.get("success") and result.get("cookies"):
                        logger.debug("Successfully obtained cookies from addon")
                        return result["cookies"]
                    if result.get("captcha_required"):
                        logger.info(
                            "reCAPTCHA challenge triggered — retry usually resolves it"
                        )
                        return CAPTCHA_REQUIRED
                    logger.error(
                        "Addon login failed: %s",
                        result.get("error", "Unknown error"),
                    )
                    return None
                else:
                    logger.error("Addon request failed with status %s", resp.status)
                    return None

    except Exception as e:
        logger.error("Failed to get cookies from addon: %s", e)
        return None
