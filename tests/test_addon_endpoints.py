"""Tests for the add-on's FastAPI endpoints (run.py)."""

import json
import sys
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

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
    async def test_profile_status_contract(self):
        """Phase D: /profile-status returns required keys (profile_created_at, profile_last_success_at, etc.)."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/profile-status")
            assert resp.status_code == 200
            data = resp.json()
            assert "profile_created_at" in data
            assert "profile_last_success_at" in data
            assert "recent_captcha_count" in data
            assert "profile_size_bytes" in data
            assert "warmup_state" in data
            assert data["warmup_state"] in ("idle", "warming", "ready", "failed")

    @pytest.mark.asyncio
    async def test_login_failures_artifact_endpoint_contract(self):
        """Task 2: /artifacts/login-failures returns metadata-only listing payload."""
        payload = {
            "count": 1,
            "items": [
                {
                    "id": "1773000000000",
                    "created_at": "2026-03-06T00:00:00+00:00",
                    "category": "unknown_runtime_error",
                    "subreason": "site_flow_changed",
                    "url": "https://mysmartenergy.psegliny.com/",
                    "title": "MySmartEnergy",
                    "recaptcha_iframe": False,
                    "html_file": "1773000000000/page.html",
                    "screenshot_file": "1773000000000/page.png",
                }
            ],
        }
        with patch("run.list_login_failure_artifacts", return_value=payload, create=True):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/artifacts/login-failures?limit=5")

        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["items"][0]["id"] == "1773000000000"
        assert "html" not in data["items"][0]

    @pytest.mark.asyncio
    async def test_startup_prunes_login_failure_artifacts(self):
        """Task 2: add-on startup should trigger artifact retention pruning once."""
        with patch("run.prune_login_failure_artifacts", create=True) as mock_prune:
            from run import startup_maintenance

            await startup_maintenance()

        mock_prune.assert_called()

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
    async def test_login_failure_exposes_structured_category(self):
        """Task 1: login endpoint should propagate addon failure category/subreason."""
        with patch("run.get_fresh_cookies", new_callable=AsyncMock) as mock_login:
            mock_login.return_value = {
                "cookies": None,
                "category": "transient_site_error",
                "subreason": "upstream_503",
                "error": "Upstream site unavailable",
                "captcha_required": False,
            }
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/login", json={
                    "username": "test@example.com",
                    "password": "testpass",
                })

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["category"] == "transient_site_error"
        assert data["subreason"] == "upstream_503"

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


@pytest.mark.skipif(not HAS_HTTPX, reason="httpx not installed")
class TestDebugAutoDisable:
    """Tests for Task 7: debug auto-disable lifecycle controls."""

    @pytest.mark.asyncio
    async def test_debug_status_endpoint_exists(self):
        """GET /debug-status returns current debug state."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/debug-status")
        assert resp.status_code == 200
        data = resp.json()
        assert "debug_enabled" in data
        assert "auto_disable_hours" in data
        assert "debug_enabled_at" in data
        assert "auto_disable_at" in data

    @pytest.mark.asyncio
    async def test_debug_state_persisted_to_data_dir(self):
        """Debug state file is written under /data when debug is enabled."""
        from run import _save_debug_state, _load_debug_state, DEBUG_STATE_PATH

        state = {
            "debug_enabled": True,
            "debug_enabled_at": time.time(),
            "auto_disable_hours": 24,
        }
        with patch("builtins.open", create=True) as mock_open:
            mock_open.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_open.return_value.__exit__ = MagicMock(return_value=False)
            _save_debug_state(state)
        mock_open.assert_called_once()

    @pytest.mark.asyncio
    async def test_auto_disable_flips_log_level_at_runtime(self):
        """When auto-disable fires, logging level switches from DEBUG to INFO."""
        from run import _check_auto_disable

        state = {
            "debug_enabled": True,
            "debug_enabled_at": time.time() - (25 * 3600),  # 25 hours ago
            "auto_disable_hours": 24,
        }
        with patch("run._load_debug_state", return_value=state), \
             patch("run._save_debug_state") as mock_save, \
             patch("run.logging") as mock_logging:
            mock_root = MagicMock()
            mock_logging.getLogger.return_value = mock_root
            mock_logging.INFO = 20

            result = _check_auto_disable()

        assert result is True  # auto-disable fired
        mock_save.assert_called_once()
        saved = mock_save.call_args[0][0]
        assert saved["debug_enabled"] is False

    @pytest.mark.asyncio
    async def test_auto_disable_does_nothing_when_disabled(self):
        """When auto_disable_hours is 0, no auto-disable occurs."""
        from run import _check_auto_disable

        state = {
            "debug_enabled": True,
            "debug_enabled_at": time.time() - (100 * 3600),
            "auto_disable_hours": 0,
        }
        with patch("run._load_debug_state", return_value=state), \
             patch("run._save_debug_state") as mock_save:
            result = _check_auto_disable()

        assert result is False  # did not fire
        mock_save.assert_not_called()

    @pytest.mark.asyncio
    async def test_auto_disable_does_nothing_when_debug_off(self):
        """When debug is already off, auto-disable is a no-op."""
        from run import _check_auto_disable

        state = {
            "debug_enabled": False,
            "debug_enabled_at": None,
            "auto_disable_hours": 24,
        }
        with patch("run._load_debug_state", return_value=state), \
             patch("run._save_debug_state") as mock_save:
            result = _check_auto_disable()

        assert result is False
        mock_save.assert_not_called()

    @pytest.mark.asyncio
    async def test_config_yaml_has_auto_disable_option(self):
        """config.yaml should include debug_auto_disable_hours in schema."""
        import yaml

        config_path = os.path.join(
            os.path.dirname(__file__), "..", "addons", "psegli-automation", "config.yaml"
        )
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)

        assert "debug_auto_disable_hours" in config.get("schema", {})
        assert config["options"]["debug_auto_disable_hours"] == 0

    @pytest.mark.asyncio
    async def test_restart_after_auto_disable_stays_at_info(self):
        """Restart with debug:true after auto-disable should NOT re-enable debug.

        _apply_debug_startup_state must return False when persisted state shows
        auto-disable already fired (debug_enabled=False, debug_enabled_at set).
        """
        from run import _apply_debug_startup_state

        prior_state = {
            "debug_enabled": False,
            "debug_enabled_at": time.time() - (25 * 3600),
            "auto_disable_hours": 24,
        }

        with patch("run._load_debug_state", return_value=prior_state), \
             patch("run._save_debug_state") as mock_save:
            result = _apply_debug_startup_state(debug_from_config=True, auto_disable_hours=24)

        assert result is False, "Restart after auto-disable must not re-arm debug"
        mock_save.assert_not_called()

    @pytest.mark.asyncio
    async def test_fresh_debug_cycle_after_toggle_off_and_on(self):
        """Toggling debug off then on should start a fresh auto-disable cycle.

        When debug is turned off, _apply_debug_startup_state clears
        debug_enabled_at. When turned back on, a new timestamp is recorded.
        """
        from run import _apply_debug_startup_state

        # Step 1: debug turned off — clears debug_enabled_at
        auto_disabled_state = {
            "debug_enabled": False,
            "debug_enabled_at": time.time() - 3600,
            "auto_disable_hours": 24,
        }
        with patch("run._load_debug_state", return_value=auto_disabled_state), \
             patch("run._save_debug_state") as mock_save:
            result = _apply_debug_startup_state(debug_from_config=False, auto_disable_hours=24)

        assert result is False
        mock_save.assert_called_once()
        saved = mock_save.call_args[0][0]
        assert saved["debug_enabled_at"] is None, "debug-off should clear timestamp"

        # Step 2: debug turned back on with cleared state — fresh cycle
        cleared_state = {
            "debug_enabled": False,
            "debug_enabled_at": None,
            "auto_disable_hours": 24,
        }
        with patch("run._load_debug_state", return_value=cleared_state), \
             patch("run._save_debug_state") as mock_save2, \
             patch("run._check_auto_disable", return_value=False):
            result2 = _apply_debug_startup_state(debug_from_config=True, auto_disable_hours=24)

        assert result2 is True, "Fresh enable should return debug=True"
        mock_save2.assert_called_once()
        saved2 = mock_save2.call_args[0][0]
        assert saved2["debug_enabled"] is True
        assert saved2["debug_enabled_at"] is not None
