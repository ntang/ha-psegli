#!/usr/bin/env python3
"""Automated login for PSEG Long Island using the automation addon."""

import logging
import aiohttp
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Sentinel for MFA required - caller should use complete_mfa_login(code)
MFA_REQUIRED = "MFA_REQUIRED"

async def check_addon_health() -> bool:
    """Check if the addon is available and healthy."""
    try:
        logger.debug("Checking addon health...")
        
        async with aiohttp.ClientSession() as session:
            # Check if addon is available via direct port access
            try:
                async with session.get("http://localhost:8000/health", timeout=5) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        if result.get("status") == "healthy":
                            logger.debug("Addon is healthy and available")
                            return True
                        else:
                            logger.debug("Addon responded but status is not healthy")
                            return False
                    else:
                        logger.debug(f"Addon health check failed with status {resp.status}")
                        return False
            except Exception as e:
                logger.debug(f"Addon health check failed: {e}")
                return False
                
    except Exception as e:
        logger.debug(f"Error checking addon health: {e}")
        return False

async def get_fresh_cookies(
    username: str,
    password: str,
    mfa_code: Optional[str] = None,
    mfa_method: str = "sms",
) -> Optional[str]:
    """Get fresh cookies using the automation addon.
    
    Returns:
        Cookie string on success, MFA_REQUIRED when MFA is needed (call complete_mfa_login),
        or None on failure.
    """
    try:
        logger.debug("Requesting fresh cookies from PSEG automation addon...")
        
        # First check if addon is healthy
        if not await check_addon_health():
            logger.warning("Addon not available or unhealthy, cannot get fresh cookies")
            return None
        
        # Try to connect to the addon
        async with aiohttp.ClientSession() as session:
            login_data = {
                "username": username,
                "password": password,
                "mfa_method": mfa_method or "sms",
            }
            if mfa_code:
                login_data["mfa_code"] = mfa_code
            
            logger.debug("Sending login request to addon with timeout=120s...")
            
            async with session.post(
                "http://localhost:8000/login",
                json=login_data,
                timeout=120  # Extended timeout to match addon processing time
            ) as resp:
                logger.debug(f"Addon response received: status={resp.status}")
                if resp.status == 200:
                    result = await resp.json()
                    logger.debug(f"Addon response: {result}")
                    if result.get("success") and result.get("cookies"):
                        logger.debug("Successfully obtained cookies from addon")
                        return result["cookies"]
                    if result.get("mfa_required"):
                        logger.info("PSEG MFA required - use complete_mfa_login(code) with code from email or SMS")
                        return MFA_REQUIRED
                    logger.error(f"Addon login failed: {result.get('error', 'Unknown error')}")
                    return None
                else:
                    logger.error(f"Addon request failed with status {resp.status}")
                    return None
                    
    except Exception as e:
        logger.error(f"Failed to get cookies from addon: {e}")
        return None


async def complete_mfa_login(code: str) -> Optional[str]:
    """Complete login after MFA - provide the verification code from your email or SMS.
    
    Call this after get_fresh_cookies returns MFA_REQUIRED. The addon keeps the
    session alive for a few minutes waiting for the code.
    """
    try:
        logger.debug("Sending MFA code to addon...")
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "http://localhost:8000/login/mfa",
                json={"code": code},
                timeout=120,
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    if result.get("success") and result.get("cookies"):
                        logger.debug("MFA successful, cookies obtained")
                        return result["cookies"]
                logger.error(f"MFA failed: {await resp.text()}")
                return None
    except Exception as e:
        logger.error(f"Failed to complete MFA: {e}")
        return None
