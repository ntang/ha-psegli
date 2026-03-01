#!/usr/bin/env python3
"""Test v2: direct mysmartenergy login with playwright-stealth + persistent profile."""

import asyncio
import json
import os
import sys
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

# Persistent profile directory — reCAPTCHA trusts returning visitors more
PROFILE_DIR = os.path.join(os.path.dirname(__file__), ".browser_profile")


async def test_direct_login(email: str, password: str):
    print("Starting Playwright (headed, persistent profile)...")
    pw = await async_playwright().start()

    stealth = Stealth()
    context = await pw.chromium.launch_persistent_context(
        user_data_dir=PROFILE_DIR,
        headless=False,
        args=[
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
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

    page = context.pages[0] if context.pages else await context.new_page()
    await stealth.apply_stealth_async(page)

    # Track the login AJAX response
    login_response_data = {}

    async def capture_login_response(response):
        if "/Home/Login" in response.url and response.request.method == "POST":
            try:
                body = await response.json()
                login_response_data.update(body)
                print(f"  Login API response: {json.dumps(body, indent=2)}")
            except Exception:
                print(f"  Login API response (non-JSON): status={response.status}")

    page.on("response", capture_login_response)

    try:
        # Step 1: Navigate to mysmartenergy
        print("Navigating to mysmartenergy.psegliny.com/Dashboard ...")
        await page.goto(
            "https://mysmartenergy.psegliny.com/Dashboard",
            wait_until="domcontentloaded",
        )
        await asyncio.sleep(2)
        print(f"  Current URL: {page.url}")

        # Check if already logged in (persistent profile has valid session)
        login_form_visible = await page.query_selector("#LoginEmail")
        if not login_form_visible:
            print("Already logged in from previous session!")
            await print_cookies(context)
            print("\nBrowser staying open 30s to inspect...")
            await asyncio.sleep(30)
            return

        # Step 2: Fill login form
        print("Filling login form...")
        await page.fill("#LoginEmail", email)
        await asyncio.sleep(0.5)
        await page.fill("#LoginPassword", password)
        await asyncio.sleep(0.5)

        # Toggle "Remember Me" checkbox
        remember_me = await page.query_selector("#RememberMe")
        if remember_me:
            checked = await remember_me.is_checked()
            if not checked:
                await remember_me.click()
                print("  Remember Me: toggled ON")
            else:
                print("  Remember Me: already ON")
        else:
            print("  Remember Me: checkbox not found")

        await asyncio.sleep(0.5)
        print("  Credentials entered.")

        # Step 3: Click login button (triggers invisible reCAPTCHA)
        print("Clicking login button...")
        await page.click(".loginBtn")

        # Step 4: Wait for the AJAX login response
        # The login is AJAX-based — the page doesn't navigate on submit.
        # On success, JS redirects to /Dashboard. On failure, it shows an error.
        print("Waiting for reCAPTCHA + login (up to 60s)...")
        print("  (If a CAPTCHA image grid appears, solve it manually)")

        # Wait for either: page navigates to /Dashboard, or we get a login response
        for i in range(60):
            await asyncio.sleep(1)
            if login_response_data:
                break
            if "/Dashboard" in page.url and page.url != "https://mysmartenergy.psegliny.com/":
                print(f"  Redirected to: {page.url}")
                break

        await asyncio.sleep(2)

        # Step 5: Report results
        print("\n--- Results ---")
        await print_cookies(context)

        if login_response_data:
            data = login_response_data.get("Data", {})
            error = data.get("LoginErrorMessage", "")
            if error:
                print(f"Login error: {error}")
            elif data.get("ChangePasswordSuccessUrl"):
                print("Login succeeded (server returned redirect URL)")
            else:
                print("Login response received (check details above)")

        login_form_still_visible = await page.query_selector("#LoginEmail")
        if login_form_still_visible:
            print("Status: NOT LOGGED IN (login form still visible)")
        else:
            print("Status: LOGGED IN")

        print(f"Final URL: {page.url}")

        # Keep browser open to inspect
        print("\nBrowser staying open 60s so you can inspect...")
        print("(Profile saved to .browser_profile/ for next run)")
        await asyncio.sleep(60)

    finally:
        await context.close()
        await pw.stop()


async def print_cookies(context):
    cookies = await context.cookies()
    mm_sid = next((c for c in cookies if c["name"] == "MM_SID"), None)
    req_token = next(
        (c for c in cookies if c["name"] == "__RequestVerificationToken"), None
    )
    if mm_sid:
        print(f"MM_SID: YES (length={len(mm_sid['value'])})")
    else:
        print("MM_SID: NOT FOUND")
    if req_token:
        print(f"__RequestVerificationToken: YES (length={len(req_token['value'])})")
    else:
        print("__RequestVerificationToken: NOT FOUND")

    # Print and save cookie string for API testing
    if mm_sid and req_token:
        cookie_str = f"MM_SID={mm_sid['value']}; __RequestVerificationToken={req_token['value']}"
        print(f"\nCookie string for API testing:\n  {cookie_str}")
        cookie_file = os.path.join(os.path.dirname(__file__), ".cookie")
        with open(cookie_file, "w") as f:
            f.write(cookie_str)
        print(f"  Saved to {cookie_file}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python test_direct_login_v2.py EMAIL PASSWORD")
        sys.exit(1)
    asyncio.run(test_direct_login(sys.argv[1], sys.argv[2]))
