"""Tests for the add-on's FastAPI endpoints (run.py)."""

import sys
import os
from unittest.mock import AsyncMock, patch

import pytest

# Add the addon directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "addons", "psegli-automation"))

from run import app

# Use httpx for testing FastAPI
try:
    from httpx import AsyncClient, ASGITransport
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False


@pytest.mark.skipif(not HAS_HTTPX, reason="httpx not installed")
class TestFastAPIEndpoints:
    """Tests for FastAPI endpoints in run.py."""

    @pytest.mark.asyncio
    async def test_health_check(self):
        """Health endpoint returns healthy status."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "healthy"
            assert data["service"] == "psegli-automation"

    @pytest.mark.asyncio
    async def test_no_mfa_endpoint(self):
        """Verify /login/mfa returns 404 (removed)."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/login/mfa", json={"code": "123456"})
            assert resp.status_code in (404, 405)

    @pytest.mark.asyncio
    async def test_login_success(self):
        """Login endpoint returns cookies on success."""
        with patch("run.get_fresh_cookies", new_callable=AsyncMock) as mock_login:
            mock_login.return_value = "MM_SID=abc123; __RequestVerificationToken=xyz789"
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/login", json={
                    "username": "test@example.com",
                    "password": "testpass",
                })
                assert resp.status_code == 200
                data = resp.json()
                assert data["success"] is True
                assert "MM_SID=" in data["cookies"]

    @pytest.mark.asyncio
    async def test_login_captcha_required(self):
        """Login endpoint signals CAPTCHA required."""
        with patch("run.get_fresh_cookies", new_callable=AsyncMock) as mock_login:
            mock_login.return_value = "CAPTCHA_REQUIRED"
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/login", json={
                    "username": "test@example.com",
                    "password": "testpass",
                })
                assert resp.status_code == 200
                data = resp.json()
                assert data["success"] is False
                assert data["captcha_required"] is True

    @pytest.mark.asyncio
    async def test_login_failure(self):
        """Login endpoint returns error on failure."""
        with patch("run.get_fresh_cookies", new_callable=AsyncMock) as mock_login:
            mock_login.return_value = None
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/login", json={
                    "username": "test@example.com",
                    "password": "badpass",
                })
                assert resp.status_code == 200
                data = resp.json()
                assert data["success"] is False

    @pytest.mark.asyncio
    async def test_concurrent_login_serialized(self):
        """Two concurrent /login calls should be serialized by the lock."""
        import asyncio
        call_order = []

        async def slow_login(**kwargs):
            call_order.append("start")
            await asyncio.sleep(0.1)
            call_order.append("end")
            return "MM_SID=test"

        with patch("run.get_fresh_cookies", side_effect=slow_login):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                # Fire two login requests concurrently
                tasks = [
                    client.post("/login", json={"username": "a@b.com", "password": "p"}),
                    client.post("/login", json={"username": "a@b.com", "password": "p"}),
                ]
                await asyncio.gather(*tasks)

        # With the lock, we should see start/end/start/end (serialized)
        # Without the lock, we'd see start/start/end/end
        assert call_order == ["start", "end", "start", "end"]
