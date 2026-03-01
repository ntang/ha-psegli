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
