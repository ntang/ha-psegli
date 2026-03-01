#!/usr/bin/env python3
"""Test if a saved cookie is still valid by hitting the Dashboard API."""

import os
import sys
import requests
from datetime import datetime, timedelta

COOKIE_FILE = os.path.join(os.path.dirname(__file__), ".cookie")


def load_cookie() -> str:
    """Load cookie from .cookie file or CLI argument."""
    if len(sys.argv) > 1:
        return sys.argv[1]
    if os.path.exists(COOKIE_FILE):
        with open(COOKIE_FILE) as f:
            cookie = f.read().strip()
        if cookie:
            print(f"Loaded cookie from {COOKIE_FILE}")
            return cookie
    print(f"No cookie found. Either pass as argument or run test_direct_login_v2.py first.")
    print(f"Usage: python test_cookie_validity.py ['MM_SID=...; __RequestVerificationToken=...']")
    sys.exit(1)


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


if __name__ == "__main__":
    cookie = load_cookie()
    test_cookie(cookie)
