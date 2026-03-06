"""Tests for config_flow.py (config and options flows)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.psegli.auto_login import (
    LoginResult,
    CATEGORY_CAPTCHA_REQUIRED,
    CATEGORY_ADDON_DISCONNECT,
)
from custom_components.psegli.config_flow import PSEGLIConfigFlow, PSEGLIOptionsFlow
from custom_components.psegli.const import (
    DOMAIN,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_COOKIE,
    CONF_ADDON_URL,
    DEFAULT_ADDON_URL,
    CONF_DIAGNOSTIC_LEVEL,
    CONF_NOTIFICATION_LEVEL,
    DIAGNOSTIC_STANDARD,
    NOTIFICATION_CRITICAL_ONLY,
)
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

    @patch("custom_components.psegli.config_flow.check_addon_health", new_callable=AsyncMock)
    @patch("custom_components.psegli.config_flow.get_fresh_cookies", new_callable=AsyncMock)
    async def test_user_step_captcha_shows_error(self, mock_fresh, mock_health, mock_hass):
        """CAPTCHA from addon shows captcha_required error."""
        mock_fresh.return_value = LoginResult(category=CATEGORY_CAPTCHA_REQUIRED)
        mock_health.return_value = True

        flow = _make_config_flow(mock_hass)
        result = await flow.async_step_user({
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "pass",
        })

        assert result["type"] == "form"
        assert result["errors"]["base"] == "captcha_required"
        assert result["description_placeholders"]["preflight_status"] == "ready"

    @patch("custom_components.psegli.config_flow.PSEGLIClient")
    @patch("custom_components.psegli.config_flow.get_fresh_cookies", new_callable=AsyncMock)
    async def test_user_step_no_cookie_addon_provides_one(
        self, mock_fresh, mock_client_cls, mock_hass
    ):
        """No cookie submitted, addon provides one → entry created."""
        mock_fresh.return_value = LoginResult(cookies="MM_SID=addon_cookie")
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
        assert result["data"][CONF_ADDON_URL] == DEFAULT_ADDON_URL
        mock_fresh.assert_called_once_with(
            "user@example.com",
            "pass",
            addon_url=DEFAULT_ADDON_URL,
        )

    @patch("custom_components.psegli.config_flow.get_fresh_cookies", new_callable=AsyncMock)
    async def test_user_step_no_cookie_addon_fails_still_creates_entry(
        self, mock_fresh, mock_hass
    ):
        """No cookie and addon fails → entry created with empty cookie (setup will handle)."""
        mock_fresh.return_value = LoginResult(category=CATEGORY_ADDON_DISCONNECT)

        flow = _make_config_flow(mock_hass)
        result = await flow.async_step_user({
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "pass",
        })

        assert result["type"] == "create_entry"
        assert result["data"][CONF_COOKIE] == ""

    @patch("custom_components.psegli.config_flow.check_addon_health", new_callable=AsyncMock)
    async def test_user_step_shows_form_on_first_visit(self, mock_health, mock_hass):
        """First visit (no input) shows the form with preflight status."""
        mock_health.return_value = True
        flow = _make_config_flow(mock_hass)
        result = await flow.async_step_user(None)

        assert result["type"] == "form"
        assert result["step_id"] == "user"
        assert "description_placeholders" in result
        assert result["description_placeholders"]["preflight_status"] == "ready"

    @patch("custom_components.psegli.config_flow.check_addon_health", new_callable=AsyncMock)
    async def test_user_step_preflight_unreachable_shows_remediation(self, mock_health, mock_hass):
        """Phase G: When add-on is unreachable, form shows unreachable status and message."""
        mock_health.return_value = False
        flow = _make_config_flow(mock_hass)
        result = await flow.async_step_user(None)

        assert result["type"] == "form"
        assert result["description_placeholders"]["preflight_status"] == "unreachable"
        assert "not reachable" in result["description_placeholders"]["preflight_message"]
        assert "Install and start" in result["description_placeholders"]["preflight_message"]

    @patch("custom_components.psegli.config_flow.check_addon_health", new_callable=AsyncMock)
    @patch("custom_components.psegli.config_flow.PSEGLIClient")
    async def test_user_step_preflight_uses_submitted_addon_url_on_form_rerender(
        self, mock_client_cls, mock_health, mock_hass
    ):
        """Preflight should evaluate the user-submitted addon URL on rerender."""
        custom_url = "http://my-addon-host:8000"
        mock_health.return_value = False
        mock_client = MagicMock()
        mock_client.test_connection = MagicMock(side_effect=InvalidAuth("bad cookie"))
        mock_client_cls.return_value = mock_client

        flow = _make_config_flow(mock_hass)
        result = await flow.async_step_user(
            {
                CONF_USERNAME: "user@example.com",
                CONF_PASSWORD: "pass",
                CONF_COOKIE: "MM_SID=bad",
                CONF_ADDON_URL: custom_url,
            }
        )

        assert result["type"] == "form"
        assert result["errors"]["base"] == "invalid_auth"
        mock_health.assert_awaited_with(custom_url)

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
        custom_url = "http://addon.example:8000"
        mock_config_entry.options = {CONF_ADDON_URL: custom_url}
        mock_fresh.return_value = LoginResult(cookies="MM_SID=addon_refreshed")
        mock_client = MagicMock()
        mock_client.test_connection = MagicMock(return_value=True)
        mock_client_cls.return_value = mock_client

        flow = _make_options_flow(mock_hass, mock_config_entry)
        result = await flow.async_step_init({CONF_COOKIE: ""})

        assert result["type"] == "create_entry"
        mock_fresh.assert_called_once_with(
            mock_config_entry.data[CONF_USERNAME],
            mock_config_entry.data[CONF_PASSWORD],
            addon_url=custom_url,
        )

    @patch("custom_components.psegli.config_flow.get_fresh_cookies", new_callable=AsyncMock)
    @patch("custom_components.psegli.config_flow.PSEGLIClient")
    async def test_options_promotes_discovered_working_addon_url(
        self, mock_client_cls, mock_fresh, mock_hass, mock_config_entry
    ):
        """If fallback URL succeeds, options should persist discovered URL."""
        provided_url = "http://localhost:8000"
        discovered_url = "http://84ee8c30-psegli-automation:8000"
        mock_fresh.return_value = LoginResult(
            cookies="MM_SID=addon_refreshed",
            addon_url=discovered_url,
        )
        mock_client = MagicMock()
        mock_client.test_connection = MagicMock(return_value=True)
        mock_client_cls.return_value = mock_client

        flow = _make_options_flow(mock_hass, mock_config_entry)
        result = await flow.async_step_init(
            {CONF_COOKIE: "", CONF_ADDON_URL: provided_url}
        )

        assert result["type"] == "create_entry"
        assert result["data"][CONF_ADDON_URL] == discovered_url

    @patch("custom_components.psegli.config_flow.get_fresh_cookies", new_callable=AsyncMock)
    async def test_options_no_cookie_captcha_still_saves_options(
        self, mock_fresh, mock_hass, mock_config_entry
    ):
        """CAPTCHA during options addon fetch should not block saving options."""
        mock_fresh.return_value = LoginResult(category=CATEGORY_CAPTCHA_REQUIRED)

        flow = _make_options_flow(mock_hass, mock_config_entry)
        result = await flow.async_step_init({CONF_COOKIE: ""})

        assert result["type"] == "create_entry"

    @patch("custom_components.psegli.config_flow.get_fresh_cookies", new_callable=AsyncMock)
    async def test_options_no_cookie_addon_failure_still_saves_addon_url(
        self, mock_fresh, mock_hass, mock_config_entry
    ):
        """Addon refresh failure should not block saving addon_url/options."""
        custom_url = "http://84ee8c30-psegli-automation:8000"
        mock_fresh.return_value = LoginResult(category="invalid_credentials")

        flow = _make_options_flow(mock_hass, mock_config_entry)
        result = await flow.async_step_init(
            {
                CONF_COOKIE: "",
                CONF_ADDON_URL: custom_url,
                CONF_DIAGNOSTIC_LEVEL: "verbose",
                CONF_NOTIFICATION_LEVEL: "verbose",
            }
        )

        assert result["type"] == "create_entry"
        assert result["data"][CONF_ADDON_URL] == custom_url
        assert result["data"][CONF_DIAGNOSTIC_LEVEL] == "verbose"
        assert result["data"][CONF_NOTIFICATION_LEVEL] == "verbose"

    async def test_options_no_credentials_still_saves_options(self, mock_hass):
        """No credentials should not block saving non-auth options."""
        entry = MagicMock()
        entry.data = {CONF_USERNAME: "", CONF_PASSWORD: "", CONF_COOKIE: ""}

        flow = _make_options_flow(mock_hass, entry)
        result = await flow.async_step_init({CONF_COOKIE: ""})

        assert result["type"] == "create_entry"

    async def test_options_shows_form_on_first_visit(self, mock_hass, mock_config_entry):
        """First visit (no input) shows the options form."""
        flow = _make_options_flow(mock_hass, mock_config_entry)
        result = await flow.async_step_init(None)

        assert result["type"] == "form"
        assert result["step_id"] == "init"

    @patch("custom_components.psegli.config_flow.PSEGLIClient")
    async def test_options_persists_observability_options(
        self, mock_client_cls, mock_hass, mock_config_entry
    ):
        """Observability options (diagnostic_level, notification_level) are persisted."""
        mock_client = MagicMock()
        mock_client.test_connection = MagicMock(return_value=True)
        mock_client_cls.return_value = mock_client

        flow = _make_options_flow(mock_hass, mock_config_entry)
        result = await flow.async_step_init({
            CONF_COOKIE: "MM_SID=new",
            CONF_DIAGNOSTIC_LEVEL: "verbose",
            CONF_NOTIFICATION_LEVEL: "verbose",
        })

        assert result["type"] == "create_entry"
        assert result["data"][CONF_DIAGNOSTIC_LEVEL] == "verbose"
        assert result["data"][CONF_NOTIFICATION_LEVEL] == "verbose"

    @patch("custom_components.psegli.config_flow.PSEGLIClient")
    async def test_options_defaults_observability_when_not_provided(
        self, mock_client_cls, mock_hass, mock_config_entry
    ):
        """Observability options default to standard/critical_only when not submitted."""
        mock_client = MagicMock()
        mock_client.test_connection = MagicMock(return_value=True)
        mock_client_cls.return_value = mock_client

        flow = _make_options_flow(mock_hass, mock_config_entry)
        result = await flow.async_step_init({
            CONF_COOKIE: "MM_SID=new",
        })

        assert result["type"] == "create_entry"
        assert result["data"][CONF_DIAGNOSTIC_LEVEL] == DIAGNOSTIC_STANDARD
        assert result["data"][CONF_NOTIFICATION_LEVEL] == NOTIFICATION_CRITICAL_ONLY

    async def test_options_schema_includes_observability_fields(self, mock_hass, mock_config_entry):
        """Options schema includes diagnostic_level and notification_level."""
        flow = _make_options_flow(mock_hass, mock_config_entry)
        result = await flow.async_step_init(None)

        # The form should have been shown with the schema
        schema = result["data_schema"]
        schema_keys = [str(k) for k in schema.schema]
        assert CONF_ADDON_URL in schema_keys
        assert CONF_DIAGNOSTIC_LEVEL in schema_keys
        assert CONF_NOTIFICATION_LEVEL in schema_keys

    async def test_options_schema_defaults_addon_url(self, mock_hass, mock_config_entry):
        """Options schema defaults addon_url to the integration default."""
        flow = _make_options_flow(mock_hass, mock_config_entry)
        result = await flow.async_step_init(None)
        schema = result["data_schema"]
        key = next(k for k in schema.schema if str(k) == CONF_ADDON_URL)
        assert key.default() == DEFAULT_ADDON_URL
