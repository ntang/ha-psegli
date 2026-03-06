"""Tests for the integration-side auto_login module (retry logic)."""

import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

import aiohttp
import pytest

from custom_components.psegli.auto_login import (
    CAPTCHA_REQUIRED,
    CATEGORY_ADDON_DISCONNECT,
    CATEGORY_CAPTCHA_REQUIRED,
    CATEGORY_INVALID_CREDENTIALS,
    CATEGORY_UNKNOWN_ERROR,
    LoginResult,
    get_fresh_cookies,
    _attempt_login,
    _MAX_LOGIN_RETRIES,
)


def _mock_response(status=200, json_data=None):
    """Build a mock aiohttp response with request_info and history."""
    mock_resp = AsyncMock()
    mock_resp.status = status
    mock_resp.json = AsyncMock(return_value=json_data or {})
    mock_resp.request_info = MagicMock()
    mock_resp.history = ()
    return mock_resp


def _mock_session(mock_resp):
    """Build a mock aiohttp session that returns mock_resp from post()."""
    mock_session = AsyncMock()
    mock_session.post = MagicMock(
        return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_resp),
            # return_value=False so exceptions inside `async with` propagate
            __aexit__=AsyncMock(return_value=False),
        )
    )
    return mock_session


class TestAttemptLogin:
    """Tests for the single-attempt _attempt_login helper."""

    @pytest.mark.asyncio
    async def test_returns_cookies_on_success(self):
        """Successful addon response returns LoginResult with cookies."""
        resp = _mock_response(200, {"success": True, "cookies": "MM_SID=abc; __RequestVerificationToken=xyz"})
        result = await _attempt_login(_mock_session(resp), {"username": "u", "password": "p"})
        assert isinstance(result, LoginResult)
        assert result.cookies == "MM_SID=abc; __RequestVerificationToken=xyz"
        assert result.category is None

    @pytest.mark.asyncio
    async def test_returns_captcha_category(self):
        """CAPTCHA response returns LoginResult with captcha category."""
        resp = _mock_response(200, {"captcha_required": True})
        result = await _attempt_login(_mock_session(resp), {"username": "u", "password": "p"})
        assert isinstance(result, LoginResult)
        assert result.cookies is None
        assert result.category == CATEGORY_CAPTCHA_REQUIRED

    @pytest.mark.asyncio
    async def test_returns_invalid_credentials_on_login_error(self):
        """Functional login failure (invalid creds) returns invalid_credentials category."""
        resp = _mock_response(200, {"error": "Invalid credentials"})
        result = await _attempt_login(_mock_session(resp), {"username": "u", "password": "p"})
        assert isinstance(result, LoginResult)
        assert result.cookies is None
        assert result.category == CATEGORY_INVALID_CREDENTIALS

    @pytest.mark.asyncio
    async def test_raises_on_5xx(self):
        """5xx server error raises ClientResponseError (retryable)."""
        resp = _mock_response(503)
        with pytest.raises(aiohttp.ClientResponseError) as exc_info:
            await _attempt_login(_mock_session(resp), {"username": "u", "password": "p"})
        assert exc_info.value.status == 503

    @pytest.mark.asyncio
    async def test_returns_unknown_error_on_4xx(self):
        """4xx client error returns unknown_error category (terminal, not retried)."""
        resp = _mock_response(400)
        result = await _attempt_login(_mock_session(resp), {"username": "u", "password": "p"})
        assert isinstance(result, LoginResult)
        assert result.cookies is None
        assert result.category == CATEGORY_UNKNOWN_ERROR


class TestGetFreshCookiesRetry:
    """Tests for transport-failure retry behavior in get_fresh_cookies."""

    @pytest.mark.asyncio
    @patch("custom_components.psegli.auto_login._attempt_login", new_callable=AsyncMock)
    @patch("custom_components.psegli.auto_login.asyncio.sleep", new_callable=AsyncMock)
    async def test_retries_on_transport_failure_then_succeeds(self, mock_sleep, mock_attempt):
        """Transport failure on first attempt, success on second."""
        mock_attempt.side_effect = [
            aiohttp.ServerDisconnectedError("Server disconnected"),
            LoginResult(cookies="MM_SID=abc; __RequestVerificationToken=xyz"),
        ]

        result = await get_fresh_cookies("user", "pass")

        assert result.cookies == "MM_SID=abc; __RequestVerificationToken=xyz"
        assert mock_attempt.call_count == 2
        mock_sleep.assert_called_once()  # backoff between attempts

    @pytest.mark.asyncio
    @patch("custom_components.psegli.auto_login._attempt_login", new_callable=AsyncMock)
    @patch("custom_components.psegli.auto_login.asyncio.sleep", new_callable=AsyncMock)
    async def test_retries_on_timeout_then_succeeds(self, mock_sleep, mock_attempt):
        """Timeout on first attempt, success on second."""
        mock_attempt.side_effect = [
            asyncio.TimeoutError(),
            LoginResult(cookies="MM_SID=abc; __RequestVerificationToken=xyz"),
        ]

        result = await get_fresh_cookies("user", "pass")

        assert result.cookies == "MM_SID=abc; __RequestVerificationToken=xyz"
        assert mock_attempt.call_count == 2

    @pytest.mark.asyncio
    @patch("custom_components.psegli.auto_login._attempt_login", new_callable=AsyncMock)
    @patch("custom_components.psegli.auto_login.asyncio.sleep", new_callable=AsyncMock)
    async def test_retries_on_5xx_then_succeeds(self, mock_sleep, mock_attempt):
        """5xx from addon on first attempt, success on second."""
        mock_attempt.side_effect = [
            aiohttp.ClientResponseError(MagicMock(), (), status=503, message="Server error"),
            LoginResult(cookies="MM_SID=abc; __RequestVerificationToken=xyz"),
        ]

        result = await get_fresh_cookies("user", "pass")

        assert result.cookies == "MM_SID=abc; __RequestVerificationToken=xyz"
        assert mock_attempt.call_count == 2
        mock_sleep.assert_called_once()

    @pytest.mark.asyncio
    @patch("custom_components.psegli.auto_login._attempt_login", new_callable=AsyncMock)
    @patch("custom_components.psegli.auto_login.asyncio.sleep", new_callable=AsyncMock)
    async def test_exhausts_retries_returns_disconnect_category(self, mock_sleep, mock_attempt):
        """All retries fail with transport errors — returns addon_disconnect category."""
        mock_attempt.side_effect = aiohttp.ServerDisconnectedError("Server disconnected")

        result = await get_fresh_cookies("user", "pass")

        assert result.cookies is None
        assert result.category == CATEGORY_ADDON_DISCONNECT
        assert mock_attempt.call_count == _MAX_LOGIN_RETRIES
        assert mock_sleep.call_count == _MAX_LOGIN_RETRIES - 1

    @pytest.mark.asyncio
    @patch("custom_components.psegli.auto_login._attempt_login", new_callable=AsyncMock)
    @patch("custom_components.psegli.auto_login.asyncio.sleep", new_callable=AsyncMock)
    async def test_does_not_retry_captcha(self, mock_sleep, mock_attempt):
        """CAPTCHA response is terminal — no retry."""
        mock_attempt.return_value = LoginResult(category=CATEGORY_CAPTCHA_REQUIRED)

        result = await get_fresh_cookies("user", "pass")

        assert result.cookies is None
        assert result.category == CATEGORY_CAPTCHA_REQUIRED
        assert mock_attempt.call_count == 1
        mock_sleep.assert_not_called()

    @pytest.mark.asyncio
    @patch("custom_components.psegli.auto_login._attempt_login", new_callable=AsyncMock)
    @patch("custom_components.psegli.auto_login.asyncio.sleep", new_callable=AsyncMock)
    async def test_does_not_retry_invalid_credentials(self, mock_sleep, mock_attempt):
        """Invalid credentials is terminal — no retry."""
        mock_attempt.return_value = LoginResult(category=CATEGORY_INVALID_CREDENTIALS)

        result = await get_fresh_cookies("user", "pass")

        assert result.cookies is None
        assert result.category == CATEGORY_INVALID_CREDENTIALS
        assert mock_attempt.call_count == 1
        mock_sleep.assert_not_called()

    @pytest.mark.asyncio
    @patch("custom_components.psegli.auto_login._attempt_login", new_callable=AsyncMock)
    @patch("custom_components.psegli.auto_login.asyncio.sleep", new_callable=AsyncMock)
    async def test_does_not_retry_success(self, mock_sleep, mock_attempt):
        """Successful response on first attempt — no retry."""
        mock_attempt.return_value = LoginResult(cookies="MM_SID=abc; __RequestVerificationToken=xyz")

        result = await get_fresh_cookies("user", "pass")

        assert result.cookies == "MM_SID=abc; __RequestVerificationToken=xyz"
        assert result.category is None
        assert mock_attempt.call_count == 1
        mock_sleep.assert_not_called()

    @pytest.mark.asyncio
    @patch("custom_components.psegli.auto_login._attempt_login", new_callable=AsyncMock)
    @patch("custom_components.psegli.auto_login.asyncio.sleep", new_callable=AsyncMock)
    async def test_no_health_check_gate(self, mock_sleep, mock_attempt):
        """get_fresh_cookies does not call check_addon_health internally.

        Callers that want fast-fail have their own external health checks.
        Removing the internal gate ensures transient /health failures don't
        bypass the retry loop.
        """
        mock_attempt.return_value = LoginResult(cookies="MM_SID=abc; __RequestVerificationToken=xyz")

        with patch("custom_components.psegli.auto_login.check_addon_health", new_callable=AsyncMock) as mock_health:
            result = await get_fresh_cookies("user", "pass")
            mock_health.assert_not_called()

        assert result.cookies == "MM_SID=abc; __RequestVerificationToken=xyz"

    @pytest.mark.asyncio
    @patch("custom_components.psegli.auto_login._attempt_login", new_callable=AsyncMock)
    @patch("custom_components.psegli.auto_login.asyncio.sleep", new_callable=AsyncMock)
    async def test_does_not_retry_unexpected_exception(self, mock_sleep, mock_attempt):
        """Non-transport exceptions (e.g. ValueError) are not retried."""
        mock_attempt.side_effect = ValueError("unexpected")

        result = await get_fresh_cookies("user", "pass")

        assert result.cookies is None
        assert result.category == CATEGORY_UNKNOWN_ERROR
        assert mock_attempt.call_count == 1
        mock_sleep.assert_not_called()

    @pytest.mark.asyncio
    @patch("custom_components.psegli.auto_login._attempt_login", new_callable=AsyncMock)
    @patch("custom_components.psegli.auto_login.asyncio.sleep", new_callable=AsyncMock)
    async def test_backoff_delay_increases_with_attempt(self, mock_sleep, mock_attempt):
        """Backoff delay should increase with each attempt (base * attempt + jitter)."""
        mock_attempt.side_effect = aiohttp.ServerDisconnectedError("Server disconnected")

        await get_fresh_cookies("user", "pass")

        # Should have slept (_MAX_LOGIN_RETRIES - 1) times between retries
        assert mock_sleep.call_count == _MAX_LOGIN_RETRIES - 1
        delays = [call.args[0] for call in mock_sleep.call_args_list]
        # Each delay should be at least base * attempt (jitter adds more)
        for i, delay in enumerate(delays):
            attempt = i + 1  # attempts 1, 2, ...
            assert delay >= 2.0 * attempt  # base_delay * attempt

    @pytest.mark.asyncio
    @patch("custom_components.psegli.auto_login._attempt_login", new_callable=AsyncMock)
    @patch("custom_components.psegli.auto_login.asyncio.sleep", new_callable=AsyncMock)
    async def test_retries_connection_error(self, mock_sleep, mock_attempt):
        """aiohttp.ClientConnectorError is retried."""
        connector_error = aiohttp.ClientConnectorError(
            connection_key=MagicMock(), os_error=OSError("Connection refused"),
        )
        mock_attempt.side_effect = [
            connector_error,
            LoginResult(cookies="MM_SID=abc; __RequestVerificationToken=xyz"),
        ]

        result = await get_fresh_cookies("user", "pass")

        assert result.cookies == "MM_SID=abc; __RequestVerificationToken=xyz"
        assert mock_attempt.call_count == 2

    @pytest.mark.asyncio
    @patch("custom_components.psegli.auto_login._attempt_login", new_callable=AsyncMock)
    @patch("custom_components.psegli.auto_login.asyncio.sleep", new_callable=AsyncMock)
    async def test_probes_fallback_addon_url_when_localhost_disconnects(
        self, mock_sleep, mock_attempt
    ):
        """When localhost transport fails, login should probe alternate addon URLs."""
        seen_urls: list[str] = []

        async def _side_effect(*_args, **kwargs):
            addon_url = kwargs["addon_url"]
            seen_urls.append(addon_url)
            if addon_url == "http://localhost:8000":
                raise aiohttp.ServerDisconnectedError("Server disconnected")
            return LoginResult(cookies="MM_SID=abc; __RequestVerificationToken=xyz")

        mock_attempt.side_effect = _side_effect

        result = await get_fresh_cookies(
            "user",
            "pass",
            addon_url="http://localhost:8000",
        )

        assert result.cookies == "MM_SID=abc; __RequestVerificationToken=xyz"
        assert result.addon_url == "http://84ee8c30-psegli-automation:8000"
        assert seen_urls[0] == "http://localhost:8000"
        assert any("psegli-automation" in url for url in seen_urls[1:])
        assert mock_sleep.call_count == 1
