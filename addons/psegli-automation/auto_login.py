#!/usr/bin/env python3
"""
PSEG Long Island Auto Login — Direct mysmartenergy.psegliny.com login.

Uses Playwright with stealth patches to log in directly to the mysmartenergy
portal, bypassing the myaccount/Okta/MFA chain entirely. The mysmartenergy
login form uses Google invisible reCAPTCHA which typically auto-resolves
after a few visits with a persistent browser profile.
"""

import asyncio
import json
import logging
import os
from enum import Enum
from typing import Optional

from playwright.async_api import async_playwright, BrowserContext, Page
from playwright_stealth import Stealth

_LOGGER = logging.getLogger(__name__)

# Default persistent browser profile location
DEFAULT_PROFILE_DIR = os.path.join(os.path.dirname(__file__), ".browser_profile")

# URLs
LOGIN_URL = "https://mysmartenergy.psegliny.com/Dashboard"
LOGIN_API_PATH = "/Home/Login"


class LoginResult(str, Enum):
    """Result of a login attempt."""
    SUCCESS = "success"
    FAILED = "failed"
    CAPTCHA_REQUIRED = "captcha_required"


class PSEGAutoLogin:
    """PSEG Long Island login via mysmartenergy.psegliny.com."""

    def __init__(
        self,
        email: str,
        password: str,
        headless: bool = True,
        profile_dir: Optional[str] = None,
    ):
        self.email = email
        self.password = password
        self.headless = headless
        self.profile_dir = profile_dir or DEFAULT_PROFILE_DIR
        self.playwright = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

    async def setup_browser(self) -> bool:
        """Initialize Playwright with stealth and persistent profile."""
        try:
            _LOGGER.info("Initializing Playwright browser...")
            self.playwright = await async_playwright().start()

            stealth = Stealth()
            self.context = await self.playwright.chromium.launch_persistent_context(
                user_data_dir=self.profile_dir,
                headless=self.headless,
                args=[
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
                viewport={"width": 1920, "height": 1080},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/138.0.0.0 Safari/537.36"
                ),
                locale="en-US",
                timezone_id="America/New_York",
            )

            self.page = (
                self.context.pages[0]
                if self.context.pages
                else await self.context.new_page()
            )

            await stealth.apply_stealth_async(self.page)
            self.page.set_default_timeout(30000)

            _LOGGER.info("Browser initialized successfully")
            return True

        except Exception as e:
            _LOGGER.error("Failed to setup browser: %s", e)
            await self.cleanup()
            return False

    async def login(self) -> tuple[LoginResult, Optional[str]]:
        """
        Log in to mysmartenergy and return cookies.

        Returns:
            Tuple of (LoginResult, cookie_string_or_None)
        """
        # Track the login AJAX response
        login_response = {}

        async def on_response(response):
            if LOGIN_API_PATH in response.url and response.request.method == "POST":
                try:
                    body = await response.json()
                    login_response.update(body)
                except Exception:
                    login_response["_status"] = response.status

        self.page.on("response", on_response)

        try:
            # Navigate to mysmartenergy (shows login form if unauthenticated)
            _LOGGER.info("Navigating to mysmartenergy login...")
            await self.page.goto(LOGIN_URL, wait_until="domcontentloaded")
            await asyncio.sleep(2)

            # Check if already authenticated (persistent profile session still valid)
            login_form = await self.page.query_selector("#LoginEmail")
            if not login_form:
                _LOGGER.info("Already authenticated from previous session")
                cookie_str = await self._extract_cookies()
                if cookie_str:
                    return LoginResult.SUCCESS, cookie_str
                # Session expired despite no login form — fall through to re-login
                _LOGGER.warning("No login form but no valid cookies — session may be stale")

            # Fill login form
            _LOGGER.info("Filling login form...")
            await self.page.fill("#LoginEmail", self.email)
            await asyncio.sleep(0.5)
            await self.page.fill("#LoginPassword", self.password)
            await asyncio.sleep(0.5)

            # Toggle "Remember Me"
            remember_me = await self.page.query_selector("#RememberMe")
            if remember_me and not await remember_me.is_checked():
                await remember_me.click()

            await asyncio.sleep(0.5)

            # Click login (triggers invisible reCAPTCHA)
            _LOGGER.info("Submitting login (reCAPTCHA will process)...")
            await self.page.click(".loginBtn")

            # Wait for the AJAX login response or page navigation
            for _ in range(60):
                await asyncio.sleep(1)
                if login_response:
                    break
                # Check if we navigated to the dashboard (successful login)
                if "/Dashboard" in self.page.url and self.page.url != LOGIN_URL:
                    break

            await asyncio.sleep(1)

            # Evaluate result
            if login_response:
                data = login_response.get("Data", {})
                error_msg = data.get("LoginErrorMessage", "")

                if "captcha" in error_msg.lower():
                    _LOGGER.warning("CAPTCHA challenge required — manual intervention needed")
                    return LoginResult.CAPTCHA_REQUIRED, None

                if error_msg:
                    _LOGGER.error("Login failed: %s", error_msg)
                    return LoginResult.FAILED, None

            # Check if we're on the authenticated dashboard
            login_form_still = await self.page.query_selector("#LoginEmail")
            if login_form_still:
                _LOGGER.error("Login failed — still on login page")
                return LoginResult.FAILED, None

            # Extract cookies
            cookie_str = await self._extract_cookies()
            if cookie_str:
                _LOGGER.info("Login successful, cookies obtained")
                return LoginResult.SUCCESS, cookie_str

            _LOGGER.warning("Login appeared to succeed but no cookies found")
            return LoginResult.FAILED, None

        except Exception as e:
            _LOGGER.error("Login error: %s", e)
            return LoginResult.FAILED, None

    async def _extract_cookies(self) -> Optional[str]:
        """Extract MM_SID and __RequestVerificationToken from browser context."""
        cookies = await self.context.cookies()
        cookie_dict = {}
        for cookie in cookies:
            if cookie["name"] in ("MM_SID", "__RequestVerificationToken"):
                cookie_dict[cookie["name"]] = cookie["value"]
                _LOGGER.info(
                    "Captured cookie: %s (length=%d)",
                    cookie["name"],
                    len(cookie["value"]),
                )

        if "MM_SID" in cookie_dict:
            parts = [f"{k}={v}" for k, v in cookie_dict.items()]
            return "; ".join(parts)
        return None

    async def get_cookies(self) -> Optional[str]:
        """
        Full login flow: setup browser, login, return cookies.

        Returns:
            Cookie string "MM_SID=...; __RequestVerificationToken=..." or None.
            Returns "CAPTCHA_REQUIRED" if manual CAPTCHA solving is needed.
        """
        try:
            if not await self.setup_browser():
                return None

            result, cookies = await self.login()

            if result == LoginResult.CAPTCHA_REQUIRED:
                return "CAPTCHA_REQUIRED"
            if result == LoginResult.SUCCESS:
                return cookies
            return None

        except Exception as e:
            _LOGGER.error("Error getting cookies: %s", e)
            return None
        finally:
            await self.cleanup()

    async def cleanup(self):
        """Clean up browser resources. Safe to call multiple times."""
        try:
            if self.context:
                await self.context.close()
        except Exception as e:
            _LOGGER.debug("Error closing context: %s", e)
        finally:
            self.context = None
            self.page = None

        try:
            if self.playwright:
                await self.playwright.stop()
        except Exception as e:
            _LOGGER.debug("Error stopping playwright: %s", e)
        finally:
            self.playwright = None


# --- Public API (used by run.py and integration) ---


async def get_fresh_cookies(
    username: str,
    password: str,
    headless: bool = True,
    **kwargs,
) -> Optional[str]:
    """
    Get fresh PSEG cookies via mysmartenergy login.

    Returns:
        Cookie string, "CAPTCHA_REQUIRED", or None on failure.
    """
    _LOGGER.info("Login attempt for user: %s", username)
    login = PSEGAutoLogin(email=username, password=password, headless=headless)
    return await login.get_cookies()


# --- CLI for standalone testing ---

if __name__ == "__main__":
    import argparse
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    parser = argparse.ArgumentParser(description="PSEG Long Island Auto Login")
    parser.add_argument("--email", required=True, help="PSEG account email")
    parser.add_argument("--password", required=True, help="PSEG account password")
    parser.add_argument(
        "--headed", action="store_true", help="Run with visible browser"
    )
    args = parser.parse_args()

    async def _main():
        login = PSEGAutoLogin(
            email=args.email,
            password=args.password,
            headless=not args.headed,
        )
        cookies = await login.get_cookies()
        if cookies == "CAPTCHA_REQUIRED":
            _LOGGER.error("CAPTCHA required — run with --headed and solve manually")
            return 1
        if cookies:
            _LOGGER.info("Cookies obtained successfully (length=%d)", len(cookies))
            return 0
        _LOGGER.error("Failed to obtain cookies")
        return 1

    sys.exit(asyncio.run(_main()))
