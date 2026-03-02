"""Tests for config_flow.py (config and options flows)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.psegli.config_flow import PSEGLIConfigFlow, PSEGLIOptionsFlow
from custom_components.psegli.const import DOMAIN, CONF_USERNAME, CONF_PASSWORD, CONF_COOKIE
from custom_components.psegli.exceptions import InvalidAuth, PSEGLIError


# ---------------------------------------------------------------------------
# Helper to create a flow with a mock hass
# ---------------------------------------------------------------------------

def _make_config_flow(hass):
    """Create a PSEGLIConfigFlow with injected hass."""
    flow = PSEGLIConfigFlow()
    flow.hass = hass

    # unique_id is a read-only property backed by _unique_id
    async def _set_unique_id(uid):
        flow._unique_id = uid
    flow.async_set_unique_id = _set_unique_id
    flow._abort_if_unique_id_configured = MagicMock()

    return flow


def _make_options_flow(hass, config_entry):
    """Create a PSEGLIOptionsFlow with injected hass."""
    flow = PSEGLIOptionsFlow()
    flow.hass = hass
    # HA now injects config entry via internal _config_entry on options flows.
    flow._config_entry = config_entry
    return flow


# ---------------------------------------------------------------------------
# PSEGLIConfigFlow
# ---------------------------------------------------------------------------

class TestPSEGLIConfigFlow:
    """Tests for the config flow."""

    @patch("custom_components.psegli.config_flow.PSEGLIClient")
    @patch("custom_components.psegli.config_flow.get_fresh_cookies", new_callable=AsyncMock)
    async def test_user_step_with_valid_cookie_creates_entry(
        self, mock_fresh, mock_client_cls, mock_hass
    ):
        """User submits valid credentials + cookie → entry created."""
        mock_client = MagicMock()
        mock_client.test_connection = MagicMock(return_value=True)
        mock_client_cls.return_value = mock_client

        flow = _make_config_flow(mock_hass)
        result = await flow.async_step_user({
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "pass",
            CONF_COOKIE: "MM_SID=valid",
        })

        assert result["type"] == "create_entry"
        assert result["data"][CONF_COOKIE] == "MM_SID=valid"

    @patch("custom_components.psegli.config_flow.PSEGLIClient")
    @patch("custom_components.psegli.config_flow.get_fresh_cookies", new_callable=AsyncMock)
    async def test_user_step_invalid_auth_shows_error(
        self, mock_fresh, mock_client_cls, mock_hass
    ):
        """Invalid cookie shows invalid_auth error."""
        mock_client = MagicMock()
        mock_client.test_connection = MagicMock(side_effect=InvalidAuth("bad"))
        mock_client_cls.return_value = mock_client

        flow = _make_config_flow(mock_hass)
        result = await flow.async_step_user({
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "pass",
            CONF_COOKIE: "MM_SID=bad",
        })

        assert result["type"] == "form"
        assert result["errors"]["base"] == "invalid_auth"

    @patch("custom_components.psegli.config_flow.PSEGLIClient")
    @patch("custom_components.psegli.config_flow.get_fresh_cookies", new_callable=AsyncMock)
    async def test_user_step_network_error_shows_cannot_connect(
        self, mock_fresh, mock_client_cls, mock_hass
    ):
        """Network error shows cannot_connect error."""
        mock_client = MagicMock()
        mock_client.test_connection = MagicMock(side_effect=PSEGLIError("timeout"))
        mock_client_cls.return_value = mock_client

        flow = _make_config_flow(mock_hass)
        result = await flow.async_step_user({
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "pass",
            CONF_COOKIE: "MM_SID=something",
        })

        assert result["type"] == "form"
        assert result["errors"]["base"] == "cannot_connect"

    @patch("custom_components.psegli.config_flow.get_fresh_cookies", new_callable=AsyncMock)
    async def test_user_step_captcha_shows_error(self, mock_fresh, mock_hass):
        """CAPTCHA from addon shows captcha_required error."""
        mock_fresh.return_value = "CAPTCHA_REQUIRED"

        flow = _make_config_flow(mock_hass)
        result = await flow.async_step_user({
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "pass",
        })

        assert result["type"] == "form"
        assert result["errors"]["base"] == "captcha_required"

    @patch("custom_components.psegli.config_flow.PSEGLIClient")
    @patch("custom_components.psegli.config_flow.get_fresh_cookies", new_callable=AsyncMock)
    async def test_user_step_no_cookie_addon_provides_one(
        self, mock_fresh, mock_client_cls, mock_hass
    ):
        """No cookie submitted, addon provides one → entry created."""
        mock_fresh.return_value = "MM_SID=addon_cookie"
        mock_client = MagicMock()
        mock_client.test_connection = MagicMock(return_value=True)
        mock_client_cls.return_value = mock_client

        flow = _make_config_flow(mock_hass)
        result = await flow.async_step_user({
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "pass",
        })

        assert result["type"] == "create_entry"
        assert result["data"][CONF_COOKIE] == "MM_SID=addon_cookie"

    @patch("custom_components.psegli.config_flow.get_fresh_cookies", new_callable=AsyncMock)
    async def test_user_step_no_cookie_addon_fails_still_creates_entry(
        self, mock_fresh, mock_hass
    ):
        """No cookie and addon fails → entry created with empty cookie (setup will handle)."""
        mock_fresh.return_value = None

        flow = _make_config_flow(mock_hass)
        result = await flow.async_step_user({
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "pass",
        })

        assert result["type"] == "create_entry"
        assert result["data"][CONF_COOKIE] == ""

    async def test_user_step_shows_form_on_first_visit(self, mock_hass):
        """First visit (no input) shows the form."""
        flow = _make_config_flow(mock_hass)
        result = await flow.async_step_user(None)

        assert result["type"] == "form"
        assert result["step_id"] == "user"

    async def test_single_instance_enforcement(self, mock_hass):
        """Second config flow is aborted by unique ID."""
        flow = _make_config_flow(mock_hass)
        # Simulate _abort_if_unique_id_configured raising
        flow._abort_if_unique_id_configured = MagicMock(
            side_effect=Exception("Already configured")
        )

        with pytest.raises(Exception, match="Already configured"):
            await flow.async_step_user(None)


# ---------------------------------------------------------------------------
# PSEGLIOptionsFlow
# ---------------------------------------------------------------------------

class TestPSEGLIOptionsFlow:
    """Tests for the options flow."""

    async def test_options_flow_uses_ha_managed_config_entry(self, mock_config_entry):
        """Options flow should be constructible without passing config_entry."""
        flow = PSEGLIOptionsFlow()
        flow._config_entry = mock_config_entry

        assert flow.config_entry is mock_config_entry

    @patch("custom_components.psegli.config_flow.PSEGLIClient")
    async def test_options_with_new_cookie_validates_and_persists(
        self, mock_client_cls, mock_hass, mock_config_entry
    ):
        """Submitting a new cookie validates it and persists to config entry."""
        mock_client = MagicMock()
        mock_client.test_connection = MagicMock(return_value=True)
        mock_client_cls.return_value = mock_client

        flow = _make_options_flow(mock_hass, mock_config_entry)
        result = await flow.async_step_init({CONF_COOKIE: "MM_SID=new_cookie"})

        assert result["type"] == "create_entry"
        mock_hass.config_entries.async_update_entry.assert_called_once()
        update_call = mock_hass.config_entries.async_update_entry.call_args
        assert update_call[1]["data"][CONF_COOKIE] == "MM_SID=new_cookie"

    @patch("custom_components.psegli.config_flow.PSEGLIClient")
    async def test_options_invalid_cookie_shows_error(
        self, mock_client_cls, mock_hass, mock_config_entry
    ):
        """Invalid cookie in options shows invalid_auth error."""
        mock_client = MagicMock()
        mock_client.test_connection = MagicMock(side_effect=InvalidAuth("bad"))
        mock_client_cls.return_value = mock_client

        flow = _make_options_flow(mock_hass, mock_config_entry)
        result = await flow.async_step_init({CONF_COOKIE: "MM_SID=bad"})

        assert result["type"] == "form"
        assert result["errors"]["base"] == "invalid_auth"

    @patch("custom_components.psegli.config_flow.PSEGLIClient")
    async def test_options_network_error_shows_cannot_connect(
        self, mock_client_cls, mock_hass, mock_config_entry
    ):
        """Network error in options shows cannot_connect."""
        mock_client = MagicMock()
        mock_client.test_connection = MagicMock(side_effect=PSEGLIError("down"))
        mock_client_cls.return_value = mock_client

        flow = _make_options_flow(mock_hass, mock_config_entry)
        result = await flow.async_step_init({CONF_COOKIE: "MM_SID=something"})

        assert result["type"] == "form"
        assert result["errors"]["base"] == "cannot_connect"

    @patch("custom_components.psegli.config_flow.get_fresh_cookies", new_callable=AsyncMock)
    @patch("custom_components.psegli.config_flow.PSEGLIClient")
    async def test_options_no_cookie_fetches_from_addon(
        self, mock_client_cls, mock_fresh, mock_hass, mock_config_entry
    ):
        """Empty cookie in options triggers addon fetch."""
        mock_fresh.return_value = "MM_SID=addon_refreshed"
        mock_client = MagicMock()
        mock_client.test_connection = MagicMock(return_value=True)
        mock_client_cls.return_value = mock_client

        flow = _make_options_flow(mock_hass, mock_config_entry)
        result = await flow.async_step_init({CONF_COOKIE: ""})

        assert result["type"] == "create_entry"
        mock_fresh.assert_called_once()

    @patch("custom_components.psegli.config_flow.get_fresh_cookies", new_callable=AsyncMock)
    async def test_options_no_cookie_captcha_shows_error(
        self, mock_fresh, mock_hass, mock_config_entry
    ):
        """CAPTCHA during options addon fetch shows error."""
        mock_fresh.return_value = "CAPTCHA_REQUIRED"

        flow = _make_options_flow(mock_hass, mock_config_entry)
        result = await flow.async_step_init({CONF_COOKIE: ""})

        assert result["type"] == "form"
        assert result["errors"]["base"] == "captcha_required"

    async def test_options_no_credentials_shows_error(self, mock_hass):
        """No credentials in config entry shows credentials_not_found error."""
        entry = MagicMock()
        entry.data = {CONF_USERNAME: "", CONF_PASSWORD: "", CONF_COOKIE: ""}

        flow = _make_options_flow(mock_hass, entry)
        result = await flow.async_step_init({CONF_COOKIE: ""})

        assert result["type"] == "form"
        assert result["errors"]["base"] == "credentials_not_found"

    async def test_options_shows_form_on_first_visit(self, mock_hass, mock_config_entry):
        """First visit (no input) shows the options form."""
        flow = _make_options_flow(mock_hass, mock_config_entry)
        result = await flow.async_step_init(None)

        assert result["type"] == "form"
        assert result["step_id"] == "init"
