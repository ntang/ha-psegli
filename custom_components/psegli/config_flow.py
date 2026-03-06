"""Config flow for PSEG Long Island integration."""
import logging
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult

from .const import (
    DOMAIN,
    CONF_COOKIE,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_ADDON_URL,
    DEFAULT_ADDON_URL,
    CONF_DIAGNOSTIC_LEVEL,
    CONF_NOTIFICATION_LEVEL,
    DIAGNOSTIC_STANDARD,
    DIAGNOSTIC_VERBOSE,
    NOTIFICATION_CRITICAL_ONLY,
    NOTIFICATION_VERBOSE,
)
from .psegli import PSEGLIClient, PSEGLIError
from .exceptions import InvalidAuth
from .auto_login import get_fresh_cookies, CAPTCHA_REQUIRED, CATEGORY_CAPTCHA_REQUIRED

_LOGGER = logging.getLogger(__name__)


def _normalize_addon_url(value: str | None) -> str:
    """Normalize addon URL with default fallback and no trailing slash."""
    return (value or DEFAULT_ADDON_URL).rstrip("/")


class PSEGLIConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for PSEG Long Island."""

    VERSION = 1
    has_options = True

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Create the options flow."""
        return PSEGLIOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, str] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        # Only one PSEG instance allowed
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        errors = {}

        if user_input is not None:
            try:
                username = user_input[CONF_USERNAME]
                password = user_input[CONF_PASSWORD]
                cookie = user_input.get(CONF_COOKIE, "")
                addon_url = _normalize_addon_url(user_input.get(CONF_ADDON_URL))

                # If no cookie provided, try to get one from the addon
                if not cookie:
                    _LOGGER.info(
                        "No cookie provided, attempting addon login via %s",
                        addon_url,
                    )
                    try:
                        login_result = await get_fresh_cookies(
                            username,
                            password,
                            addon_url=addon_url,
                        )

                        if login_result.category == CATEGORY_CAPTCHA_REQUIRED:
                            errors["base"] = "captcha_required"
                            return self.async_show_form(
                                step_id="user",
                                data_schema=self._get_schema(),
                                errors=errors,
                            )
                        elif login_result.cookies:
                            cookie = login_result.cookies
                            _LOGGER.debug("Successfully obtained fresh cookies from addon")
                            if login_result.addon_url:
                                addon_url = _normalize_addon_url(login_result.addon_url)
                                _LOGGER.info(
                                    "Using discovered reachable addon URL during setup: %s",
                                    addon_url,
                                )
                        else:
                            _LOGGER.warning(
                                "Addon failed to get cookies (category: %s, url=%s)",
                                login_result.category,
                                addon_url,
                            )
                    except Exception as e:
                        _LOGGER.warning(
                            "Failed to get cookies from addon url=%s: %s",
                            addon_url,
                            e,
                        )

                # If we have a cookie, validate it
                if cookie:
                    client = PSEGLIClient(cookie)
                    await self.hass.async_add_executor_job(client.test_connection)
                    _LOGGER.debug("Cookie validation successful")
                else:
                    _LOGGER.debug("No cookie available, integration will require manual cookie setup")

                return self.async_create_entry(
                    title="PSEG Long Island",
                    data={
                        CONF_USERNAME: username,
                        CONF_PASSWORD: password,
                        CONF_COOKIE: cookie,
                        CONF_ADDON_URL: addon_url,
                    },
                )

            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except PSEGLIError as e:
                _LOGGER.warning("PSEG unreachable: %s", e)
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception during setup")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=self._get_schema(),
            errors=errors,
        )

    def _get_schema(self):
        """Return the schema for the config flow."""
        return vol.Schema({
            vol.Required(CONF_USERNAME): str,
            vol.Required(CONF_PASSWORD): str,
            vol.Optional(CONF_COOKIE): str,
            vol.Optional(CONF_ADDON_URL, default=DEFAULT_ADDON_URL): str,
        })


class PSEGLIOptionsFlow(config_entries.OptionsFlow):
    """PSEG Long Island options flow."""

    async def async_step_init(
        self, user_input: dict[str, str] | None = None
    ) -> FlowResult:
        """Manage the options for PSEG Long Island."""
        errors = {}

        if user_input is not None:
            try:
                username = self.config_entry.data.get(CONF_USERNAME)
                password = self.config_entry.data.get(CONF_PASSWORD)
                new_cookie = user_input.get(CONF_COOKIE, "")
                current_addon_url = _normalize_addon_url(
                    self.config_entry.options.get(
                        CONF_ADDON_URL,
                        self.config_entry.data.get(CONF_ADDON_URL),
                    )
                )
                addon_url = _normalize_addon_url(
                    user_input.get(CONF_ADDON_URL, current_addon_url)
                )

                # Always persist observability options
                options_data = {
                    CONF_ADDON_URL: addon_url,
                    CONF_DIAGNOSTIC_LEVEL: user_input.get(
                        CONF_DIAGNOSTIC_LEVEL, DIAGNOSTIC_STANDARD
                    ),
                    CONF_NOTIFICATION_LEVEL: user_input.get(
                        CONF_NOTIFICATION_LEVEL, NOTIFICATION_CRITICAL_ONLY
                    ),
                }

                # If user provided a new cookie, validate it
                if new_cookie:
                    client = PSEGLIClient(new_cookie)
                    await self.hass.async_add_executor_job(client.test_connection)
                    _LOGGER.debug("New cookie validation successful")

                    self.hass.config_entries.async_update_entry(
                        self.config_entry,
                        data={**self.config_entry.data, CONF_COOKIE: new_cookie},
                    )

                    await self.hass.services.async_call(
                        "persistent_notification",
                        "dismiss",
                        {"notification_id": "psegli_auth_failed"},
                    )

                    return self.async_create_entry(title="", data=options_data)

                # If no new cookie provided, try to get one from the addon
                elif username and password:
                    _LOGGER.info(
                        "No new cookie provided, attempting addon login via %s",
                        addon_url,
                    )
                    try:
                        login_result = await get_fresh_cookies(
                            username,
                            password,
                            addon_url=addon_url,
                        )
                        if login_result.addon_url:
                            discovered_url = _normalize_addon_url(login_result.addon_url)
                            if discovered_url != addon_url:
                                _LOGGER.info(
                                    "Promoting discovered reachable addon URL in options: %s -> %s",
                                    addon_url,
                                    discovered_url,
                                )
                            addon_url = discovered_url
                            options_data[CONF_ADDON_URL] = addon_url

                        if login_result.category == CATEGORY_CAPTCHA_REQUIRED:
                            _LOGGER.warning(
                                "Addon refresh requires CAPTCHA; saving options without cookie update"
                            )
                        elif login_result.cookies:
                            client = PSEGLIClient(login_result.cookies)
                            await self.hass.async_add_executor_job(client.test_connection)

                            self.hass.config_entries.async_update_entry(
                                self.config_entry,
                                data={**self.config_entry.data, CONF_COOKIE: login_result.cookies},
                            )

                            await self.hass.services.async_call(
                                "persistent_notification",
                                "dismiss",
                                {"notification_id": "psegli_auth_failed"},
                            )

                            _LOGGER.debug("Successfully obtained and validated fresh cookies from addon")
                        else:
                            _LOGGER.warning(
                                "Addon did not return cookies (category: %s); saving options without cookie update",
                                login_result.category,
                            )
                    except Exception as e:
                        _LOGGER.warning(
                            "Failed to get cookies from addon url=%s: %s; saving options without cookie update",
                            addon_url,
                            e,
                        )
                else:
                    _LOGGER.warning(
                        "No credentials found on config entry; saving options without cookie update"
                    )

                # Even on error, persist observability options if they changed
                if not errors:
                    return self.async_create_entry(title="", data=options_data)

            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except PSEGLIError as e:
                _LOGGER.warning("PSEG unreachable during reconfigure: %s", e)
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception during reconfigure")
                errors["base"] = "unknown"

        # Read current options for pre-filling the form
        current_diag = self.config_entry.options.get(
            CONF_DIAGNOSTIC_LEVEL, DIAGNOSTIC_STANDARD
        )
        current_notif = self.config_entry.options.get(
            CONF_NOTIFICATION_LEVEL, NOTIFICATION_CRITICAL_ONLY
        )
        current_addon_url = _normalize_addon_url(
            self.config_entry.options.get(
                CONF_ADDON_URL,
                self.config_entry.data.get(CONF_ADDON_URL),
            )
        )

        return self.async_show_form(
            step_id="init",
            data_schema=self._get_options_schema(
                current_diag,
                current_notif,
                current_addon_url,
            ),
            errors=errors,
            description_placeholders={
                "current_cookie": "Set" if self.config_entry.data.get(CONF_COOKIE) else "None"
            },
        )

    def _get_options_schema(
        self,
        current_diag: str = DIAGNOSTIC_STANDARD,
        current_notif: str = NOTIFICATION_CRITICAL_ONLY,
        current_addon_url: str = DEFAULT_ADDON_URL,
    ):
        """Return the schema for the options flow."""
        return vol.Schema({
            vol.Optional(CONF_COOKIE, description="Leave empty to attempt automatic refresh via addon"): str,
            vol.Optional(CONF_ADDON_URL, default=current_addon_url): str,
            vol.Optional(
                CONF_DIAGNOSTIC_LEVEL,
                default=current_diag,
            ): vol.In([DIAGNOSTIC_STANDARD, DIAGNOSTIC_VERBOSE]),
            vol.Optional(
                CONF_NOTIFICATION_LEVEL,
                default=current_notif,
            ): vol.In([NOTIFICATION_CRITICAL_ONLY, NOTIFICATION_VERBOSE]),
        })
