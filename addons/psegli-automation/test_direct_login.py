#!/usr/bin/env python3
"""Quick test: direct login to mysmartenergy.psegliny.com with stealth Playwright."""

import asyncio
import sys
from playwright.async_api import async_playwright


async def test_direct_login(email: str, password: str):
    print("Starting Playwright (headed)...")
    pw = await async_playwright().start()

    browser = await pw.chromium.launch(
        headless=False,
        args=[
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
        ],
    )

    context = await browser.new_context(
        viewport={"width": 1920, "height": 1080},
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/138.0.0.0 Safari/537.36"
        ),
        locale="en-US",
        timezone_id="America/New_York",
    )

    page = await context.new_page()

    # Stealth: hide navigator.webdriver
    await page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined, configurable: true
        });
    """)

    try:
        # Step 1: Go straight to mysmartenergy
        print("Navigating to mysmartenergy.psegliny.com/Dashboard ...")
        await page.goto(
            "https://mysmartenergy.psegliny.com/Dashboard",
            wait_until="domcontentloaded",
        )
        await asyncio.sleep(2)
        print(f"  Current URL: {page.url}")

        # Step 2: Fill login form
        print("Filling login form...")
        await page.fill("#LoginEmail", email)
        await page.fill("#LoginPassword", password)
        await asyncio.sleep(1)
        print("  Credentials entered.")

        # Step 3: Click login button (triggers invisible reCAPTCHA)
        print("Clicking login button...")
        await page.click(".loginBtn")

        # Step 4: Wait for either dashboard load or error
        print("Waiting for reCAPTCHA + login response (up to 30s)...")
        try:
            await page.wait_for_url(
                lambda url: "/Dashboard" in url and "/Home" not in url,
                timeout=30000,
            )
            print(f"  Landed on: {page.url}")
        except Exception as e:
            print(f"  URL wait result: {e}")
            print(f"  Current URL: {page.url}")

        await asyncio.sleep(2)

        # Step 5: Check for cookies
        cookies = await context.cookies()
        mm_sid = next(
            (c for c in cookies if c["name"] == "MM_SID"),
            None,
        )

        if mm_sid:
            print(f"\nSUCCESS — got MM_SID cookie (length={len(mm_sid['value'])})")
            print(f"  Value: {mm_sid['value'][:60]}...")
        else:
            print("\nNo MM_SID cookie found. Available cookies:")
            for c in cookies:
                print(f"  {c['name']} = {c['value'][:40]}...")

        # Check page content for error messages
        content = await page.content()
        if "LoginErrorMessage" in content or "Please provide" in content:
            print("\nLogin error detected on page.")
        if "Dashboard" in page.url and "Home" not in page.url:
            print("Appears to be on the authenticated dashboard!")

        # Keep browser open so you can inspect
        print("\nBrowser staying open for 60s so you can inspect...")
        await asyncio.sleep(60)

    finally:
        await browser.close()
        await pw.stop()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python test_direct_login.py EMAIL PASSWORD")
        sys.exit(1)
    asyncio.run(test_direct_login(sys.argv[1], sys.argv[2]))
