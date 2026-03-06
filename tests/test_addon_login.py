"""Tests for the add-on's PSEGAutoLogin class (mocked Playwright)."""

import asyncio
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add the addon directory to path so we can import from it
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "addons", "psegli-automation"))

from auto_login import PSEGAutoLogin, LoginResult, get_fresh_cookies


@pytest.fixture
def mock_playwright():
    """Create a mock Playwright environment."""
    pw = AsyncMock()
    context = AsyncMock()
    page = AsyncMock()

    # Set up page defaults
    page.url = "https://mysmartenergy.psegliny.com/Dashboard"
    page.query_selector = AsyncMock(return_value=None)  # No login form = already authenticated
    page.goto = AsyncMock()
    page.fill = AsyncMock()
    page.click = AsyncMock()
    page.on = MagicMock()
    page.remove_listener = AsyncMock()
    page.set_default_timeout = MagicMock()

    # Context with pages and cookies
    context.pages = [page]
    context.cookies = AsyncMock(return_value=[
        {"name": "MM_SID", "value": "test_session_id_abc123", "domain": ".psegliny.com"},
        {"name": "__RequestVerificationToken", "value": "test_token_xyz789", "domain": ".psegliny.com"},
    ])
    context.close = AsyncMock()

    # Playwright browser launch
    pw.chromium.launch_persistent_context = AsyncMock(return_value=context)
    pw.stop = AsyncMock()

    return pw, context, page


def _make_login_instance():
    """Helper to create PSEGAutoLogin and bypass real setup_browser."""
    return PSEGAutoLogin(email="test@example.com", password="testpass")


class TestPSEGAutoLogin:
    """Tests for PSEGAutoLogin class."""

    @pytest.mark.asyncio
    async def test_login_removes_response_listener_with_await(self, mock_playwright):
        """Response listener cleanup should be awaited for async mock implementations."""
        pw, context, page = mock_playwright
        login = _make_login_instance()

        with patch("auto_login.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=pw)
            with patch("auto_login.Stealth") as mock_stealth:
                mock_stealth.return_value.apply_stealth_async = AsyncMock()
                await login.setup_browser()
                with patch("auto_login.asyncio.sleep", new_callable=AsyncMock):
                    await login.login()

        # If remove_listener is async, it must be awaited to avoid RuntimeWarning leaks
        assert page.remove_listener.await_count == 1

    @pytest.mark.asyncio
    async def test_captcha_required_sentinel_shared_with_integration(self):
        """Addon and integration should expose the same CAPTCHA sentinel constant."""
        import auto_login as addon_auto_login
        from custom_components.psegli.auto_login import CAPTCHA_REQUIRED as integration_sentinel

        assert addon_auto_login.CAPTCHA_REQUIRED_SENTINEL == integration_sentinel

    @pytest.mark.asyncio
    async def test_navigates_to_mysmartenergy(self, mock_playwright):
        """Verify login navigates to mysmartenergy.psegliny.com/Dashboard."""
        pw, context, page = mock_playwright
        login = _make_login_instance()

        with patch("auto_login.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=pw)
            with patch("auto_login.Stealth") as mock_stealth:
                mock_stealth.return_value.apply_stealth_async = AsyncMock()
                await login.setup_browser()
                # Manually call login with asyncio.sleep patched
                with patch("auto_login.asyncio.sleep", new_callable=AsyncMock):
                    result, cookies = await login.login()

        page.goto.assert_called_once()
        call_url = page.goto.call_args[0][0]
        assert "mysmartenergy.psegliny.com/Dashboard" in call_url

    @pytest.mark.asyncio
    async def test_no_brave_search_url(self):
        """Assert no reference to search.brave.com in new code."""
        import auto_login
        import inspect
        source = inspect.getsource(auto_login)
        assert "search.brave.com" not in source
        assert "brave" not in source.lower()

    @pytest.mark.asyncio
    async def test_no_networkidle(self):
        """Assert no reference to networkidle in the code."""
        import auto_login
        import inspect
        source = inspect.getsource(auto_login)
        assert "networkidle" not in source

    @pytest.mark.asyncio
    async def test_stealth_library_used(self):
        """Verify Stealth and apply_stealth_async are used."""
        import auto_login
        import inspect
        source = inspect.getsource(auto_login)
        assert "Stealth" in source
        assert "apply_stealth_async" in source

    @pytest.mark.asyncio
    async def test_persistent_context_used(self):
        """Verify launch_persistent_context is used (not launch + new_context)."""
        import auto_login
        import inspect
        source = inspect.getsource(auto_login)
        assert "launch_persistent_context" in source

    @pytest.mark.asyncio
    async def test_login_success_returns_cookies(self, mock_playwright):
        """Test the full get_cookies flow returns cookie string on success."""
        pw, context, page = mock_playwright

        # Simulate: login form NOT present (already authenticated via persistent profile)
        page.query_selector = AsyncMock(return_value=None)

        login = _make_login_instance()

        with patch("auto_login.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=pw)
            with patch("auto_login.Stealth") as mock_stealth:
                mock_stealth.return_value.apply_stealth_async = AsyncMock()
                with patch("auto_login.asyncio.sleep", new_callable=AsyncMock):
                    result = await login.get_cookies()

        assert result is not None
        assert "MM_SID=" in result
        assert "CAPTCHA_REQUIRED" not in result

    @pytest.mark.asyncio
    async def test_login_failure_returns_none(self, mock_playwright):
        """Test that login failure returns None."""
        pw, context, page = mock_playwright

        # Login form present always (login fails)
        page.query_selector = AsyncMock(return_value=MagicMock())
        # No cookies captured (MM_SID missing)
        context.cookies = AsyncMock(return_value=[])

        login = _make_login_instance()

        with patch("auto_login.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=pw)
            with patch("auto_login.Stealth") as mock_stealth:
                mock_stealth.return_value.apply_stealth_async = AsyncMock()
                with patch("auto_login.asyncio.sleep", new_callable=AsyncMock):
                    result = await login.get_cookies()

        assert result is None

    @pytest.mark.asyncio
    async def test_captcha_required_signaled(self, mock_playwright):
        """Test that CAPTCHA error in login response returns CAPTCHA_REQUIRED."""
        pw, context, page = mock_playwright

        # Login form present (need to fill it)
        remember_me_mock = AsyncMock()
        remember_me_mock.is_checked = AsyncMock(return_value=False)

        # query_selector calls: #LoginEmail (present), #RememberMe, #LoginEmail (still present after login)
        page.query_selector = AsyncMock(side_effect=[
            MagicMock(),       # #LoginEmail present (login form shown)
            remember_me_mock,  # #RememberMe checkbox
            MagicMock(),       # #LoginEmail still present (login failed)
        ])

        # Capture the response handler and invoke it with CAPTCHA error
        response_handler = None

        def capture_on(event, handler):
            nonlocal response_handler
            if event == "response":
                response_handler = handler

        page.on = MagicMock(side_effect=capture_on)

        login = _make_login_instance()

        with patch("auto_login.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=pw)
            with patch("auto_login.Stealth") as mock_stealth:
                mock_stealth.return_value.apply_stealth_async = AsyncMock()

                # Set up browser
                assert await login.setup_browser()

                # Now manually trigger the response handler before calling login
                async def patched_sleep(duration):
                    # On the first sleep after click, fire the response handler
                    if response_handler:
                        mock_response = AsyncMock()
                        mock_response.url = "https://mysmartenergy.psegliny.com/Home/Login"
                        mock_response.request.method = "POST"
                        mock_response.json = AsyncMock(return_value={
                            "Data": {"LoginErrorMessage": "Captcha validation failed"}
                        })
                        await response_handler(mock_response)

                with patch("auto_login.asyncio.sleep", side_effect=patched_sleep):
                    result, cookies = await login.login()

        assert result == LoginResult.CAPTCHA_REQUIRED
        assert cookies is None

        await login.cleanup()

    @pytest.mark.asyncio
    async def test_cleanup_idempotent(self, mock_playwright):
        """Call cleanup() twice, no exception."""
        pw, context, page = mock_playwright
        login = _make_login_instance()
        login.playwright = pw
        login.context = context
        login.page = page

        await login.cleanup()
        # Second call should not raise
        await login.cleanup()

        assert login.playwright is None
        assert login.context is None
        assert login.page is None

    @pytest.mark.asyncio
    async def test_cookie_values_not_logged(self, mock_playwright, caplog):
        """Verify no cookie values are logged at INFO level."""
        import logging
        pw, context, page = mock_playwright
        page.query_selector = AsyncMock(return_value=None)  # Already authenticated

        login = _make_login_instance()

        with patch("auto_login.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=pw)
            with patch("auto_login.Stealth") as mock_stealth:
                mock_stealth.return_value.apply_stealth_async = AsyncMock()
                with patch("auto_login.asyncio.sleep", new_callable=AsyncMock):
                    with caplog.at_level(logging.INFO):
                        result = await login.get_cookies()

        # Check that actual cookie values are not in INFO logs
        for record in caplog.records:
            if record.levelno >= logging.INFO:
                assert "test_session_id_abc123" not in record.message
                assert "test_token_xyz789" not in record.message

    @pytest.mark.asyncio
    async def test_get_fresh_cookies_api(self):
        """Test the public get_fresh_cookies function."""
        with patch("auto_login.PSEGAutoLogin") as MockLogin:
            instance = MockLogin.return_value
            instance.get_cookies = AsyncMock(return_value="MM_SID=test123")
            result = await get_fresh_cookies(username="user", password="pass")
            assert result == "MM_SID=test123"
            MockLogin.assert_called_once_with(
                email="user", password="pass", headless=True
            )

    @pytest.mark.asyncio
    async def test_setup_browser_rotates_profile_on_launch_failure(self, mock_playwright, tmp_path):
        """Phase D: When launch fails once (simulated corruption), profile is rotated and retry succeeds."""
        pw, context, page = mock_playwright
        login = PSEGAutoLogin(
            email="test@example.com",
            password="testpass",
            profile_dir=str(tmp_path / "profile"),
        )
        os.makedirs(login.profile_dir, exist_ok=True)
        call_count = 0

        async def launch_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Simulated profile corruption")
            return context

        # Use MagicMock for chromium so launch_persistent_context is a stable reference
        pw.chromium = MagicMock()
        pw.chromium.launch_persistent_context = AsyncMock(side_effect=launch_side_effect)

        with patch("auto_login.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=pw)
            with patch("auto_login.Stealth") as mock_stealth:
                mock_stealth.return_value.apply_stealth_async = AsyncMock()
                with patch("auto_login.record_profile_created"):
                    with patch("auto_login.record_profile_failed"):
                        result = await login.setup_browser()

        assert result is True
        assert call_count == 2

    def test_rotate_profile_dir_missing_path_does_not_record_created(self, tmp_path):
        """Missing profile directory should not mark a profile as newly created."""
        login = PSEGAutoLogin(
            email="test@example.com",
            password="testpass",
            profile_dir=str(tmp_path / "missing-profile"),
        )
        assert not os.path.isdir(login.profile_dir)

        with patch("auto_login.record_profile_created") as mock_record_created:
            login._rotate_profile_dir()

        mock_record_created.assert_not_called()

    @pytest.mark.asyncio
    async def test_setup_browser_retries_without_rotation_on_non_corruption_error(
        self, mock_playwright, tmp_path
    ):
        """Transient launch failures should retry once without rotating profile."""
        pw, context, _page = mock_playwright
        login = PSEGAutoLogin(
            email="test@example.com",
            password="testpass",
            profile_dir=str(tmp_path / "profile"),
        )
        os.makedirs(login.profile_dir, exist_ok=True)
        call_count = 0

        async def launch_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Temporary launch failure")
            return context

        pw.chromium = MagicMock()
        pw.chromium.launch_persistent_context = AsyncMock(side_effect=launch_side_effect)

        with patch("auto_login.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=pw)
            with patch("auto_login.Stealth") as mock_stealth:
                mock_stealth.return_value.apply_stealth_async = AsyncMock()
            with patch.object(login, "_rotate_profile_dir") as mock_rotate:
                result = await login.setup_browser()

        assert result is True
        assert call_count == 2
        mock_rotate.assert_not_called()

    @pytest.mark.asyncio
    async def test_warmup_failure_sets_failed_state(self):
        """Warm-up should mark failed when navigation errors."""
        login = _make_login_instance()
        login.page = AsyncMock()
        login.page.goto = AsyncMock(side_effect=RuntimeError("warmup failed"))

        with patch("auto_login.set_warmup_state") as mock_set_state:
            ok = await login._warmup_profile()

        assert ok is False
        assert mock_set_state.call_args_list[0].args[0] == "warming"
        assert mock_set_state.call_args_list[-1].args[0] == "failed"

    @pytest.mark.asyncio
    async def test_get_cookies_attempts_warmup_when_profile_state_failed(self):
        """Failed warm-up state should trigger another warm-up attempt."""
        login = _make_login_instance()
        login.page = AsyncMock()

        with patch.object(login, "setup_browser", new=AsyncMock(return_value=True)):
            with patch("auto_login.load_profile_state", return_value={"warmup_state": "failed"}):
                with patch.object(login, "_warmup_profile", new=AsyncMock(return_value=True)) as mock_warmup:
                    with patch.object(
                        login,
                        "login",
                        new=AsyncMock(return_value=(LoginResult.SUCCESS, "MM_SID=test_cookie")),
                    ):
                        result = await login.get_cookies()

        assert result == "MM_SID=test_cookie"
        mock_warmup.assert_awaited_once()
