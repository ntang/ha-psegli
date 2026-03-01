#!/usr/bin/env python3
"""Extract cookies from persistent browser profile and test them against the API."""

import asyncio
import os
import requests
from datetime import datetime, timedelta
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

PROFILE_DIR = os.path.join(os.path.dirname(__file__), ".browser_profile")


async def extract_cookies():
    """Open persistent profile headlessly just to read cookies."""
    pw = await async_playwright().start()
    context = await pw.chromium.launch_persistent_context(
        user_data_dir=PROFILE_DIR,
        headless=True,
        args=["--no-sandbox"],
    )
    cookies = await context.cookies()
    await context.close()
    await pw.stop()

    mm_sid = next((c for c in cookies if c["name"] == "MM_SID"), None)
    req_token = next(
        (c for c in cookies if c["name"] == "__RequestVerificationToken"), None
    )
    return mm_sid, req_token


def test_cookie(cookie_string: str):
    """Test cookie against the mysmartenergy API."""
    session = requests.Session()
    session.headers.update({
        "Cookie": cookie_string,
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    })

    print("Testing cookie against Dashboard ...")
    resp = session.get("https://mysmartenergy.psegliny.com/Dashboard", timeout=30)
    print(f"  Status: {resp.status_code} | URL: {resp.url}")

    if "LoginEmail" in resp.text or "login" in resp.url.lower():
        print("  Result: EXPIRED / INVALID")
        return False

    print("  Result: VALID")

    print("Testing ChartData API ...")
    end = datetime.now()
    start = end - timedelta(days=1)
    session.post(
        "https://mysmartenergy.psegliny.com/Dashboard/Chart",
        data={
            "Message.ChartType": "0",
            "Message.UsageInterval": "5",
            "Message.StartDate": start.strftime("%m/%d/%Y"),
            "Message.EndDate": end.strftime("%m/%d/%Y"),
        },
        timeout=30,
    )
    chart_resp = session.get(
        "https://mysmartenergy.psegliny.com/Dashboard/ChartData",
        params={"type": "Usage"},
        timeout=30,
    )
    print(f"  ChartData status: {chart_resp.status_code}")
    try:
        data = chart_resp.json()
        series_names = [s.get("name", "?") for s in data.get("series", [])]
        points = len(data["series"][0].get("data", [])) if data.get("series") else 0
        print(f"  Series: {series_names} | Data points: {points}")
    except Exception as e:
        print(f"  Parse error: {e}")
        print(f"  Preview: {chart_resp.text[:200]}")
    return True


async def main():
    mm_sid, req_token = await extract_cookies()

    if not mm_sid or not req_token:
        print("No valid cookies found in .browser_profile/")
        print("Run test_direct_login_v2.py first to log in.")
        return

    cookie_str = f"MM_SID={mm_sid['value']}; __RequestVerificationToken={req_token['value']}"
    print(f"Extracted MM_SID (length={len(mm_sid['value'])})")
    print(f"Extracted __RequestVerificationToken (length={len(req_token['value'])})")
    print()
    test_cookie(cookie_str)


if __name__ == "__main__":
    asyncio.run(main())
