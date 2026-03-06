"""Tests for __init__.py integration lifecycle."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.psegli import (
    _process_chart_data,
    async_setup_entry,
    async_unload_entry,
    async_update_options,
    _get_active_entry,
    get_last_cumulative_kwh,
    _SIGNAL_LAST_AUTH_PROBE_AT,
    _SIGNAL_LAST_AUTH_PROBE_RESULT,
    _SIGNAL_LAST_REFRESH_ATTEMPT_AT,
    _SIGNAL_LAST_REFRESH_REASON,
    _SIGNAL_LAST_REFRESH_RESULT,
    _SIGNAL_LAST_REFRESH_FAILURE_CATEGORY,
    _SIGNAL_LAST_SUCCESSFUL_UPDATE_AT,
    _SIGNAL_LAST_SUCCESSFUL_DATAPOINT_AT,
)
from custom_components.psegli.auto_login import (
    LoginResult,
    CATEGORY_ADDON_UNREACHABLE,
    CATEGORY_CAPTCHA_REQUIRED,
    CATEGORY_UNKNOWN_ERROR,
)
from custom_components.psegli.const import (
    DOMAIN,
    CONF_COOKIE,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_ADDON_URL,
    CONF_DIAGNOSTIC_LEVEL,
    CONF_NOTIFICATION_LEVEL,
    DIAGNOSTIC_VERBOSE,
    NOTIFICATION_VERBOSE,
)
from custom_components.psegli.exceptions import InvalidAuth, PSEGLIError


def _get_registered_service_handler(mock_hass, service_name: str):
    """Extract a service callback from hass.services.async_register calls."""
    for call in mock_hass.services.async_register.call_args_list:
        if call[0][1] == service_name:
            return call[0][2]
    raise AssertionError(f"Service {service_name} not registered")


# ---------------------------------------------------------------------------
# async_setup_entry
# ---------------------------------------------------------------------------

class TestAsyncSetupEntry:
    """Tests for async_setup_entry."""

    @patch("custom_components.psegli.PSEGLIClient")
    @patch("custom_components.psegli.get_fresh_cookies", new_callable=AsyncMock)
    @patch("custom_components.psegli.check_addon_health", new_callable=AsyncMock)
    async def test_setup_with_valid_cookie(
        self, mock_health, mock_fresh, mock_client_cls, mock_hass, mock_config_entry
    ):
        """Setup with a valid cookie succeeds and stores client."""
        mock_client = MagicMock()
        mock_client.test_connection = MagicMock(return_value=True)
        mock_client.cookie = "MM_SID=valid_test_cookie"
        mock_client_cls.return_value = mock_client

        result = await async_setup_entry(mock_hass, mock_config_entry)

        assert result is True
        assert mock_hass.data[DOMAIN][mock_config_entry.entry_id] is mock_client
        # Should not have tried the addon since cookie was present
        mock_fresh.assert_not_called()

    @patch("custom_components.psegli.PSEGLIClient")
    @patch("custom_components.psegli.get_fresh_cookies", new_callable=AsyncMock)
    @patch("custom_components.psegli.check_addon_health", new_callable=AsyncMock)
    async def test_setup_invalid_auth_raises_config_entry_auth_failed(
        self, mock_health, mock_fresh, mock_client_cls, mock_hass, mock_config_entry
    ):
        """InvalidAuth during setup raises ConfigEntryAuthFailed."""
        from homeassistant.exceptions import ConfigEntryAuthFailed

        mock_client = MagicMock()
        mock_client.test_connection = MagicMock(side_effect=InvalidAuth("bad cookie"))
        mock_client_cls.return_value = mock_client

        with pytest.raises(ConfigEntryAuthFailed):
            await async_setup_entry(mock_hass, mock_config_entry)

        # Client should NOT be stored on failure
        assert mock_config_entry.entry_id not in mock_hass.data.get(DOMAIN, {})

    @patch("custom_components.psegli.PSEGLIClient")
    @patch("custom_components.psegli.get_fresh_cookies", new_callable=AsyncMock)
    @patch("custom_components.psegli.check_addon_health", new_callable=AsyncMock)
    async def test_setup_network_error_raises_config_entry_not_ready(
        self, mock_health, mock_fresh, mock_client_cls, mock_hass, mock_config_entry
    ):
        """PSEGLIError during setup raises ConfigEntryNotReady for HA retry."""
        from homeassistant.exceptions import ConfigEntryNotReady

        mock_client = MagicMock()
        mock_client.test_connection = MagicMock(side_effect=PSEGLIError("DNS failed"))
        mock_client_cls.return_value = mock_client

        with pytest.raises(ConfigEntryNotReady):
            await async_setup_entry(mock_hass, mock_config_entry)

    @patch("custom_components.psegli.PSEGLIClient")
    @patch("custom_components.psegli.get_fresh_cookies", new_callable=AsyncMock)
    @patch("custom_components.psegli.check_addon_health", new_callable=AsyncMock)
    async def test_setup_no_credentials_returns_false(
        self, mock_health, mock_fresh, mock_client_cls, mock_hass, mock_config_entry
    ):
        """Missing username/password returns False immediately."""
        mock_config_entry.data = {CONF_USERNAME: "", CONF_PASSWORD: "", CONF_COOKIE: ""}

        result = await async_setup_entry(mock_hass, mock_config_entry)

        assert result is False
        mock_client_cls.assert_not_called()

    @patch("custom_components.psegli.PSEGLIClient")
    @patch("custom_components.psegli.get_fresh_cookies", new_callable=AsyncMock)
    @patch("custom_components.psegli.check_addon_health", new_callable=AsyncMock)
    async def test_setup_no_cookie_fetches_from_addon(
        self, mock_health, mock_fresh, mock_client_cls, mock_hass, mock_config_entry_no_cookie
    ):
        """When no cookie is stored, setup fetches from addon."""
        mock_fresh.return_value = LoginResult(cookies="MM_SID=fresh_addon_cookie")
        mock_client = MagicMock()
        mock_client.test_connection = MagicMock(return_value=True)
        mock_client.cookie = "MM_SID=fresh_addon_cookie"
        mock_client_cls.return_value = mock_client

        result = await async_setup_entry(mock_hass, mock_config_entry_no_cookie)

        assert result is True
        mock_fresh.assert_called_once()
        # Cookie should be persisted AFTER validation
        mock_hass.config_entries.async_update_entry.assert_called_once()
        update_call = mock_hass.config_entries.async_update_entry.call_args
        assert update_call[1]["data"][CONF_COOKIE] == "MM_SID=fresh_addon_cookie"

    @patch("custom_components.psegli.PSEGLIClient")
    @patch("custom_components.psegli.get_fresh_cookies", new_callable=AsyncMock)
    @patch("custom_components.psegli.check_addon_health", new_callable=AsyncMock)
    async def test_setup_addon_cookie_not_persisted_before_validation(
        self, mock_health, mock_fresh, mock_client_cls, mock_hass, mock_config_entry_no_cookie
    ):
        """Phase 4.9 regression test: addon cookie must not be persisted before test_connection."""
        mock_fresh.return_value = LoginResult(cookies="MM_SID=bad_addon_cookie")
        mock_client = MagicMock()
        mock_client.test_connection = MagicMock(side_effect=InvalidAuth("rejected"))
        mock_client_cls.return_value = mock_client

        from homeassistant.exceptions import ConfigEntryAuthFailed
        with pytest.raises(ConfigEntryAuthFailed):
            await async_setup_entry(mock_hass, mock_config_entry_no_cookie)

        # Cookie must NOT have been persisted since validation failed
        mock_hass.config_entries.async_update_entry.assert_not_called()

    @patch("custom_components.psegli.PSEGLIClient")
    @patch("custom_components.psegli.get_fresh_cookies", new_callable=AsyncMock)
    @patch("custom_components.psegli.check_addon_health", new_callable=AsyncMock)
    async def test_setup_existing_cookie_not_re_persisted(
        self, mock_health, mock_fresh, mock_client_cls, mock_hass, mock_config_entry
    ):
        """When cookie came from config entry (not addon), don't write it back."""
        mock_client = MagicMock()
        mock_client.test_connection = MagicMock(return_value=True)
        mock_client.cookie = "MM_SID=valid_test_cookie"
        mock_client_cls.return_value = mock_client

        await async_setup_entry(mock_hass, mock_config_entry)

        # No update needed — cookie was already in config entry
        mock_hass.config_entries.async_update_entry.assert_not_called()

    @patch("custom_components.psegli.PSEGLIClient")
    @patch("custom_components.psegli.get_fresh_cookies", new_callable=AsyncMock)
    @patch("custom_components.psegli.check_addon_health", new_callable=AsyncMock)
    async def test_setup_registers_services(
        self, mock_health, mock_fresh, mock_client_cls, mock_hass, mock_config_entry
    ):
        """Setup registers update_statistics and refresh_cookie services."""
        mock_client = MagicMock()
        mock_client.test_connection = MagicMock(return_value=True)
        mock_client.cookie = "MM_SID=valid_test_cookie"
        mock_client_cls.return_value = mock_client

        await async_setup_entry(mock_hass, mock_config_entry)

        # Services should be registered
        register_calls = mock_hass.services.async_register.call_args_list
        registered_services = [call[0][1] for call in register_calls]
        assert "update_statistics" in registered_services
        assert "refresh_cookie" in registered_services

    @patch("custom_components.psegli.PSEGLIClient")
    @patch("custom_components.psegli.get_fresh_cookies", new_callable=AsyncMock)
    @patch("custom_components.psegli.check_addon_health", new_callable=AsyncMock)
    async def test_setup_services_not_double_registered(
        self, mock_health, mock_fresh, mock_client_cls, mock_hass, mock_config_entry
    ):
        """Services are not re-registered if already present."""
        mock_hass.services.has_service.return_value = True  # already registered
        mock_client = MagicMock()
        mock_client.test_connection = MagicMock(return_value=True)
        mock_client.cookie = "MM_SID=valid_test_cookie"
        mock_client_cls.return_value = mock_client

        await async_setup_entry(mock_hass, mock_config_entry)

        mock_hass.services.async_register.assert_not_called()

    @patch("custom_components.psegli.PSEGLIClient")
    @patch("custom_components.psegli.get_fresh_cookies", new_callable=AsyncMock)
    @patch("custom_components.psegli.check_addon_health", new_callable=AsyncMock)
    async def test_update_auth_failure_schedules_coalesced_refresh(
        self, mock_health, mock_fresh, mock_client_cls, mock_hass, mock_config_entry
    ):
        """InvalidAuth during update schedules one delayed refresh and increments counter."""
        mock_client = MagicMock()
        mock_client.test_connection = MagicMock(return_value=True)
        mock_client.cookie = "MM_SID=valid_test_cookie"
        mock_client.get_usage_data = MagicMock(side_effect=InvalidAuth("chart auth failed"))
        mock_client_cls.return_value = mock_client
        mock_hass.config_entries.async_entries.return_value = [mock_config_entry]

        await async_setup_entry(mock_hass, mock_config_entry)
        update_handler = _get_registered_service_handler(mock_hass, "update_statistics")

        await update_handler(MagicMock(data={"days_back": 0}))
        await update_handler(MagicMock(data={"days_back": 0}))

        assert mock_hass.data[DOMAIN]["_consecutive_auth_failures"] == 2
        pending = mock_hass.data[DOMAIN].get("_pending_auth_refresh_task")
        assert pending is not None
        assert not pending.done()

        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending

    @patch("custom_components.psegli.PSEGLIClient")
    @patch("custom_components.psegli.get_fresh_cookies", new_callable=AsyncMock)
    @patch("custom_components.psegli.check_addon_health", new_callable=AsyncMock)
    async def test_auth_failure_threshold_emits_loop_notification_once(
        self, mock_health, mock_fresh, mock_client_cls, mock_hass, mock_config_entry
    ):
        """Third consecutive auth failure should emit loop notification once."""
        mock_client = MagicMock()
        mock_client.test_connection = MagicMock(return_value=True)
        mock_client.cookie = "MM_SID=valid_test_cookie"
        mock_client.get_usage_data = MagicMock(side_effect=InvalidAuth("chart auth failed"))
        mock_client_cls.return_value = mock_client
        mock_hass.config_entries.async_entries.return_value = [mock_config_entry]

        await async_setup_entry(mock_hass, mock_config_entry)
        update_handler = _get_registered_service_handler(mock_hass, "update_statistics")

        await update_handler(MagicMock(data={"days_back": 0}))
        await update_handler(MagicMock(data={"days_back": 0}))
        await update_handler(MagicMock(data={"days_back": 0}))

        loop_notifications = [
            call
            for call in mock_hass.services.async_call.call_args_list
            if call.args[0] == "persistent_notification"
            and call.args[1] == "create"
            and call.args[2].get("notification_id") == "psegli_chart_auth_failed_loop"
        ]
        assert len(loop_notifications) == 1

        pending = mock_hass.data[DOMAIN].get("_pending_auth_refresh_task")
        if pending is not None and not pending.done():
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending

    @patch("custom_components.psegli.PSEGLIClient")
    @patch("custom_components.psegli.get_fresh_cookies", new_callable=AsyncMock)
    @patch("custom_components.psegli.check_addon_health", new_callable=AsyncMock, return_value=True)
    async def test_refresh_service_is_single_flight(
        self, mock_health, mock_fresh, mock_client_cls, mock_hass, mock_config_entry
    ):
        """Concurrent refresh service calls should share one in-flight refresh task."""
        started = asyncio.Event()
        release = asyncio.Event()

        async def _slow_refresh(*_args, **_kwargs):
            started.set()
            await release.wait()
            return LoginResult(cookies="MM_SID=new_cookie; __RequestVerificationToken=new_token")

        mock_fresh.side_effect = _slow_refresh

        mock_client = MagicMock()
        mock_client.test_connection = MagicMock(return_value=True)
        mock_client.cookie = "MM_SID=valid_test_cookie"
        mock_client.get_usage_data = MagicMock(return_value={"chart_data": {}})
        mock_client_cls.return_value = mock_client
        mock_hass.config_entries.async_entries.return_value = [mock_config_entry]

        await async_setup_entry(mock_hass, mock_config_entry)
        refresh_handler = _get_registered_service_handler(mock_hass, "refresh_cookie")

        call = MagicMock(data={})
        t1 = asyncio.create_task(refresh_handler(call))
        await started.wait()
        t2 = asyncio.create_task(refresh_handler(call))
        await asyncio.sleep(0)
        assert mock_fresh.call_count == 1

        release.set()
        await asyncio.gather(t1, t2)
        assert mock_fresh.call_count == 1

    @patch("custom_components.psegli.PSEGLIClient")
    @patch("custom_components.psegli.get_fresh_cookies", new_callable=AsyncMock)
    @patch("custom_components.psegli.check_addon_health", new_callable=AsyncMock)
    async def test_setup_starts_scheduled_task(
        self, mock_health, mock_fresh, mock_client_cls, mock_hass, mock_config_entry
    ):
        """Setup starts the scheduled cookie refresh task."""
        mock_client = MagicMock()
        mock_client.test_connection = MagicMock(return_value=True)
        mock_client.cookie = "MM_SID=valid_test_cookie"
        mock_client_cls.return_value = mock_client

        await async_setup_entry(mock_hass, mock_config_entry)

        assert mock_hass.data[DOMAIN].get("_scheduled_task_running") is True
        assert "_scheduled_task" in mock_hass.data[DOMAIN]

    @patch("custom_components.psegli.PSEGLIClient")
    @patch("custom_components.psegli.get_fresh_cookies", new_callable=AsyncMock)
    @patch("custom_components.psegli.check_addon_health", new_callable=AsyncMock)
    async def test_setup_uses_background_task_for_scheduler(
        self, mock_health, mock_fresh, mock_client_cls, mock_hass, mock_config_entry
    ):
        """Scheduler should use background task API so startup is not blocked."""
        mock_client = MagicMock()
        mock_client.test_connection = MagicMock(return_value=True)
        mock_client.cookie = "MM_SID=valid_test_cookie"
        mock_client_cls.return_value = mock_client

        await async_setup_entry(mock_hass, mock_config_entry)

        mock_config_entry.async_create_background_task.assert_called_once()

    @patch("custom_components.psegli.PSEGLIClient")
    @patch("custom_components.psegli.get_fresh_cookies", new_callable=AsyncMock)
    @patch("custom_components.psegli.check_addon_health", new_callable=AsyncMock)
    async def test_setup_no_cookie_no_addon_raises_config_entry_not_ready(
        self, mock_health, mock_fresh, mock_client_cls, mock_hass, mock_config_entry_no_cookie
    ):
        """No cookie and addon fails → ConfigEntryNotReady for automatic HA retry."""
        from homeassistant.exceptions import ConfigEntryNotReady

        mock_fresh.return_value = LoginResult(category="addon_disconnect")

        with pytest.raises(ConfigEntryNotReady):
            await async_setup_entry(mock_hass, mock_config_entry_no_cookie)
        # Should have sent a persistent notification
        mock_hass.services.async_call.assert_called()

    @patch("custom_components.psegli._process_chart_data", new_callable=AsyncMock)
    @patch("custom_components.psegli.PSEGLIClient")
    @patch("custom_components.psegli.get_fresh_cookies", new_callable=AsyncMock)
    @patch("custom_components.psegli.check_addon_health", new_callable=AsyncMock)
    async def test_scheduled_check_uses_data_path_probe(
        self,
        mock_health,
        mock_fresh,
        mock_client_cls,
        mock_process_chart_data,
        mock_hass,
        mock_config_entry,
    ):
        """Scheduled cookie-validity check should call test_data_path()."""
        captured_scheduler_coro = {}

        def _capture_background_task(hass, coro, name, eager_start=True):
            captured_scheduler_coro["coro"] = coro
            task = MagicMock()
            task.done.return_value = False
            task.cancel = MagicMock()
            return task

        mock_config_entry.async_create_background_task = MagicMock(
            side_effect=_capture_background_task
        )
        mock_hass.config_entries.async_entries.return_value = [mock_config_entry]

        mock_client = MagicMock()
        mock_client.cookie = "MM_SID=valid_test_cookie"
        mock_client.test_connection = MagicMock(return_value=True)
        mock_client.test_data_path = MagicMock(return_value=True)
        mock_client.get_usage_data = MagicMock(return_value={"chart_data": {}})
        mock_client_cls.return_value = mock_client

        await async_setup_entry(mock_hass, mock_config_entry)
        assert "coro" in captured_scheduler_coro

        setup_validation_calls = mock_client.test_connection.call_count

        with patch(
            "custom_components.psegli.asyncio.sleep",
            new=AsyncMock(side_effect=[None, asyncio.CancelledError()]),
        ):
            await captured_scheduler_coro["coro"]

        assert mock_client.test_data_path.call_count >= 1
        # Scheduler validity checks should use test_data_path, not test_connection.
        assert mock_client.test_connection.call_count == setup_validation_calls


# ---------------------------------------------------------------------------
# async_unload_entry
# ---------------------------------------------------------------------------

class TestAsyncUnloadEntry:
    """Tests for async_unload_entry."""

    async def test_unload_removes_client_from_hass_data(self, mock_hass, mock_config_entry):
        """Unload removes entry's client from hass.data."""
        mock_hass.data[DOMAIN] = {mock_config_entry.entry_id: MagicMock()}
        mock_config_entry.runtime_data = MagicMock()
        mock_config_entry.runtime_data.async_shutdown = AsyncMock()
        mock_hass.config_entries.async_entries.return_value = []

        result = await async_unload_entry(mock_hass, mock_config_entry)

        assert result is True
        assert mock_config_entry.entry_id not in mock_hass.data[DOMAIN]

    async def test_unload_removes_services_on_last_entry(self, mock_hass, mock_config_entry):
        """Services are removed when the last config entry is unloaded."""
        mock_hass.data[DOMAIN] = {mock_config_entry.entry_id: MagicMock()}
        mock_config_entry.runtime_data = MagicMock()
        mock_config_entry.runtime_data.async_shutdown = AsyncMock()
        # No other entries
        mock_hass.config_entries.async_entries.return_value = []

        await async_unload_entry(mock_hass, mock_config_entry)

        remove_calls = mock_hass.services.async_remove.call_args_list
        removed_services = [call[0][1] for call in remove_calls]
        assert "update_statistics" in removed_services
        assert "refresh_cookie" in removed_services

    async def test_unload_keeps_services_when_other_entries_exist(self, mock_hass, mock_config_entry):
        """Services are NOT removed when other loaded config entries remain."""
        other_entry = MagicMock()
        other_entry.entry_id = "other_entry_id"
        # Both entries loaded in hass.data; the other stays after unload
        mock_hass.data[DOMAIN] = {
            mock_config_entry.entry_id: MagicMock(),
            other_entry.entry_id: MagicMock(),
        }
        mock_config_entry.runtime_data = MagicMock()
        mock_config_entry.runtime_data.async_shutdown = AsyncMock()
        mock_hass.config_entries.async_entries.return_value = [other_entry]

        await async_unload_entry(mock_hass, mock_config_entry)

        mock_hass.services.async_remove.assert_not_called()

    async def test_unload_cancels_scheduled_task_on_last_entry(self, mock_hass, mock_config_entry):
        """Scheduled task is cancelled when the last entry is unloaded."""
        mock_task = MagicMock()
        mock_task.done.return_value = False
        mock_task.cancel = MagicMock()
        # Make await task raise CancelledError as real asyncio would
        mock_hass.data[DOMAIN] = {
            mock_config_entry.entry_id: MagicMock(),
            "_scheduled_task_running": True,
            "_scheduled_task": mock_task,
        }
        mock_config_entry.runtime_data = MagicMock()
        mock_config_entry.runtime_data.async_shutdown = AsyncMock()
        mock_hass.config_entries.async_entries.return_value = []

        # Patch the await on the task to simulate CancelledError
        async def _fake_await_task():
            raise asyncio.CancelledError()
        mock_task.__await__ = lambda self: _fake_await_task().__await__()

        await async_unload_entry(mock_hass, mock_config_entry)

        mock_task.cancel.assert_called_once()
        assert "_scheduled_task_running" not in mock_hass.data[DOMAIN]

    async def test_unload_handles_missing_runtime_data(self, mock_hass, mock_config_entry):
        """Unload handles entry without runtime_data (setup failed partway)."""
        mock_hass.data[DOMAIN] = {mock_config_entry.entry_id: MagicMock()}
        mock_config_entry.runtime_data = None
        mock_hass.config_entries.async_entries.return_value = []

        result = await async_unload_entry(mock_hass, mock_config_entry)
        assert result is True


# ---------------------------------------------------------------------------
# _get_active_entry
# ---------------------------------------------------------------------------

class TestGetActiveEntry:
    """Tests for _get_active_entry."""

    def test_returns_loaded_entry(self, mock_hass, mock_config_entry):
        """Returns the entry when it exists in hass.data."""
        mock_hass.data[DOMAIN] = {mock_config_entry.entry_id: MagicMock()}
        mock_hass.config_entries.async_entries.return_value = [mock_config_entry]

        result = _get_active_entry(mock_hass)
        assert result is mock_config_entry

    def test_returns_none_when_no_entries_loaded(self, mock_hass):
        """Returns None when no entries are loaded."""
        mock_hass.data[DOMAIN] = {}
        mock_hass.config_entries.async_entries.return_value = []

        result = _get_active_entry(mock_hass)
        assert result is None

    def test_skips_entry_not_in_hass_data(self, mock_hass, mock_config_entry):
        """Skips entries that aren't fully set up in hass.data."""
        mock_hass.data[DOMAIN] = {}  # entry_id not present
        mock_hass.config_entries.async_entries.return_value = [mock_config_entry]

        result = _get_active_entry(mock_hass)
        assert result is None


# ---------------------------------------------------------------------------
# get_last_cumulative_kwh
# ---------------------------------------------------------------------------

class TestGetLastCumulativeKwh:
    """Tests for get_last_cumulative_kwh."""

    @patch("custom_components.psegli.get_instance")
    async def test_returns_sum_from_statistics(self, mock_get_instance, mock_hass):
        """Returns the cumulative sum from the last recorded statistic."""
        mock_recorder = MagicMock()
        mock_recorder.async_add_executor_job = AsyncMock(
            return_value={"psegli:off_peak_usage": [{"sum": 123.456}]}
        )
        mock_get_instance.return_value = mock_recorder

        result = await get_last_cumulative_kwh(mock_hass, "psegli:off_peak_usage")
        assert result == 123.456

    @patch("custom_components.psegli.get_instance")
    async def test_returns_zero_when_no_history(self, mock_get_instance, mock_hass):
        """Returns 0.0 when no statistics exist."""
        mock_recorder = MagicMock()
        mock_recorder.async_add_executor_job = AsyncMock(return_value={})
        mock_get_instance.return_value = mock_recorder

        result = await get_last_cumulative_kwh(mock_hass, "psegli:off_peak_usage")
        assert result == 0.0

    @patch("custom_components.psegli.get_instance")
    async def test_returns_zero_on_exception(self, mock_get_instance, mock_hass):
        """Returns 0.0 gracefully on exception."""
        mock_recorder = MagicMock()
        mock_recorder.async_add_executor_job = AsyncMock(side_effect=Exception("DB error"))
        mock_get_instance.return_value = mock_recorder

        result = await get_last_cumulative_kwh(mock_hass, "psegli:off_peak_usage")
        assert result == 0.0


# ---------------------------------------------------------------------------
# _process_chart_data
# ---------------------------------------------------------------------------

class TestProcessChartData:
    """Tests for _process_chart_data."""

    @patch("custom_components.psegli.get_last_cumulative_kwh", new_callable=AsyncMock)
    @patch("custom_components.psegli.async_add_external_statistics", new_callable=AsyncMock)
    async def test_includes_mean_type_when_supported(
        self, mock_add_stats, mock_get_last_cumulative, mock_hass
    ):
        """Include mean_type in metadata when the HA runtime supports it."""
        mock_get_last_cumulative.return_value = 0.0
        chart_data = {
            "Off-Peak Usage": {
                "valid_points": [
                    {"timestamp": datetime(2026, 3, 1, 5, 0, tzinfo=timezone.utc), "value": 1.25}
                ]
            }
        }

        with patch("custom_components.psegli._STAT_METADATA_SUPPORTS_MEAN_TYPE", True), patch(
            "custom_components.psegli._MEAN_TYPE_NONE", 0
        ):
            await _process_chart_data(mock_hass, chart_data)

        metadata = mock_add_stats.call_args.args[1]
        assert metadata["mean_type"] == 0

    @patch("custom_components.psegli.get_last_cumulative_kwh", new_callable=AsyncMock)
    @patch("custom_components.psegli.async_add_external_statistics", new_callable=AsyncMock)
    async def test_includes_unit_class_when_supported(
        self, mock_add_stats, mock_get_last_cumulative, mock_hass
    ):
        """Include unit_class in metadata when the HA runtime supports it."""
        mock_get_last_cumulative.return_value = 0.0
        chart_data = {
            "Off-Peak Usage": {
                "valid_points": [
                    {"timestamp": datetime(2026, 3, 1, 5, 0, tzinfo=timezone.utc), "value": 1.25}
                ]
            }
        }

        with patch("custom_components.psegli._STAT_METADATA_SUPPORTS_UNIT_CLASS", True), patch(
            "custom_components.psegli._UNIT_CLASS_ENERGY", "energy"
        ):
            await _process_chart_data(mock_hass, chart_data)

        metadata = mock_add_stats.call_args.args[1]
        assert metadata["unit_class"] == "energy"


# ---------------------------------------------------------------------------
# async_update_options
# ---------------------------------------------------------------------------

class TestAsyncUpdateOptions:
    """Tests for async_update_options."""

    async def test_applies_cookie_to_live_client(self, mock_hass, mock_config_entry):
        """Cookie changes are applied to the live client and coordinator."""
        mock_client = MagicMock()
        mock_hass.data[DOMAIN] = {mock_config_entry.entry_id: mock_client}
        mock_config_entry.data = {CONF_COOKIE: "MM_SID=updated"}
        mock_coord = MagicMock()
        mock_coord.client = MagicMock()
        mock_config_entry.runtime_data = mock_coord

        await async_update_options(mock_hass, mock_config_entry)

        mock_client.update_cookie.assert_called_once_with("MM_SID=updated")
        mock_coord.client.update_cookie.assert_called_once_with("MM_SID=updated")

    async def test_no_op_when_no_cookie(self, mock_hass, mock_config_entry):
        """No action when cookie is empty."""
        mock_config_entry.data = {CONF_COOKIE: ""}
        mock_hass.data[DOMAIN] = {mock_config_entry.entry_id: MagicMock()}

        await async_update_options(mock_hass, mock_config_entry)
        # Should not have tried to update any client
        client = mock_hass.data[DOMAIN][mock_config_entry.entry_id]
        client.update_cookie.assert_not_called()

    async def test_no_op_when_entry_not_loaded(self, mock_hass, mock_config_entry):
        """No action when entry is not in hass.data (not loaded)."""
        mock_config_entry.data = {CONF_COOKIE: "MM_SID=something"}
        mock_hass.data[DOMAIN] = {}  # entry not loaded

        # Should not raise
        await async_update_options(mock_hass, mock_config_entry)

    async def test_manual_cookie_update_resets_auth_failure_counter(self, mock_hass, mock_config_entry):
        """Manual cookie updates should clear consecutive auth failures immediately."""
        mock_client = MagicMock()
        mock_hass.data[DOMAIN] = {
            mock_config_entry.entry_id: mock_client,
            "_consecutive_auth_failures": 3,
        }
        mock_config_entry.data = {CONF_COOKIE: "MM_SID=updated"}
        mock_coord = MagicMock()
        mock_coord.client = MagicMock()
        mock_config_entry.runtime_data = mock_coord

        await async_update_options(mock_hass, mock_config_entry)

        assert mock_hass.data[DOMAIN]["_consecutive_auth_failures"] == 0


# ---------------------------------------------------------------------------
# Phase 3.2/3.3: Signal tracking, get_status, observability
# ---------------------------------------------------------------------------

class TestSignalTracking:
    """Tests for Phase 3.3 signal model."""

    @patch("custom_components.psegli._process_chart_data", new_callable=AsyncMock)
    @patch("custom_components.psegli.PSEGLIClient")
    @patch("custom_components.psegli.get_fresh_cookies", new_callable=AsyncMock)
    @patch("custom_components.psegli.check_addon_health", new_callable=AsyncMock)
    async def test_successful_update_records_last_successful_update_at(
        self, mock_health, mock_fresh, mock_client_cls, mock_process, mock_hass, mock_config_entry
    ):
        """Successful statistics update records last_successful_update_at signal."""
        mock_client = MagicMock()
        mock_client.test_connection = MagicMock(return_value=True)
        mock_client.cookie = "MM_SID=valid_test_cookie"
        mock_client.get_usage_data = MagicMock(return_value={"chart_data": {}})
        mock_client_cls.return_value = mock_client
        mock_hass.config_entries.async_entries.return_value = [mock_config_entry]

        await async_setup_entry(mock_hass, mock_config_entry)
        handler = _get_registered_service_handler(mock_hass, "update_statistics")
        await handler(MagicMock(data={"days_back": 0}))

        assert _SIGNAL_LAST_SUCCESSFUL_UPDATE_AT in mock_hass.data[DOMAIN]
        assert isinstance(
            mock_hass.data[DOMAIN][_SIGNAL_LAST_SUCCESSFUL_UPDATE_AT], datetime
        )

    @patch("custom_components.psegli.PSEGLIClient")
    @patch("custom_components.psegli.get_fresh_cookies", new_callable=AsyncMock)
    @patch("custom_components.psegli.check_addon_health", new_callable=AsyncMock, return_value=False)
    async def test_refresh_failure_records_signals(
        self, mock_health, mock_fresh, mock_client_cls, mock_hass, mock_config_entry
    ):
        """Failed refresh records failure signals including category."""
        mock_client = MagicMock()
        mock_client.test_connection = MagicMock(return_value=True)
        mock_client.cookie = "MM_SID=valid_test_cookie"
        mock_client_cls.return_value = mock_client
        mock_hass.config_entries.async_entries.return_value = [mock_config_entry]

        await async_setup_entry(mock_hass, mock_config_entry)
        handler = _get_registered_service_handler(mock_hass, "refresh_cookie")
        await handler(MagicMock(data={}))

        assert mock_hass.data[DOMAIN][_SIGNAL_LAST_REFRESH_RESULT] == "failed"
        assert (
            mock_hass.data[DOMAIN][_SIGNAL_LAST_REFRESH_FAILURE_CATEGORY]
            == CATEGORY_ADDON_UNREACHABLE
        )
        assert mock_hass.data[DOMAIN][_SIGNAL_LAST_REFRESH_REASON] == "manual_service"

    @patch("custom_components.psegli.PSEGLIClient")
    @patch("custom_components.psegli.get_fresh_cookies", new_callable=AsyncMock)
    @patch("custom_components.psegli.check_addon_health", new_callable=AsyncMock)
    async def test_refresh_uses_configured_addon_url(
        self, mock_health, mock_fresh, mock_client_cls, mock_hass, mock_config_entry
    ):
        """Refresh uses configured addon URL for health + login calls."""
        custom_url = "http://addon.example:8000"
        mock_config_entry.options = {"addon_url": custom_url}
        mock_health.return_value = True
        mock_fresh.return_value = LoginResult(cookies="MM_SID=fresh_cookie")
        mock_client = MagicMock()
        mock_client.test_connection = MagicMock(return_value=True)
        mock_client.cookie = "MM_SID=valid_test_cookie"
        mock_client_cls.return_value = mock_client
        mock_hass.config_entries.async_entries.return_value = [mock_config_entry]

        await async_setup_entry(mock_hass, mock_config_entry)
        handler = _get_registered_service_handler(mock_hass, "refresh_cookie")
        await handler(MagicMock(data={}))

        mock_health.assert_called_once_with(custom_url)
        mock_fresh.assert_called_once_with(
            mock_config_entry.data[CONF_USERNAME],
            mock_config_entry.data[CONF_PASSWORD],
            addon_url=custom_url,
        )

    @patch("custom_components.psegli.PSEGLIClient")
    @patch("custom_components.psegli.get_fresh_cookies", new_callable=AsyncMock)
    @patch("custom_components.psegli.check_addon_health", new_callable=AsyncMock)
    async def test_refresh_promotes_discovered_working_addon_url(
        self, mock_health, mock_fresh, mock_client_cls, mock_hass, mock_config_entry
    ):
        """Refresh should persist a discovered working addon URL into options."""
        provided_url = "http://localhost:8000"
        discovered_url = "http://84ee8c30-psegli-automation:8000"
        mock_config_entry.options = {CONF_ADDON_URL: provided_url}
        mock_health.return_value = True
        mock_fresh.return_value = LoginResult(
            cookies="MM_SID=fresh_cookie",
            addon_url=discovered_url,
        )
        mock_client = MagicMock()
        mock_client.test_connection = MagicMock(return_value=True)
        mock_client.cookie = "MM_SID=valid_test_cookie"
        mock_client_cls.return_value = mock_client
        mock_hass.config_entries.async_entries.return_value = [mock_config_entry]

        await async_setup_entry(mock_hass, mock_config_entry)
        handler = _get_registered_service_handler(mock_hass, "refresh_cookie")
        await handler(MagicMock(data={}))

        option_updates = [
            c for c in mock_hass.config_entries.async_update_entry.call_args_list
            if "options" in c.kwargs
        ]
        assert option_updates
        assert option_updates[-1].kwargs["options"][CONF_ADDON_URL] == discovered_url

    @patch("custom_components.psegli.PSEGLIClient")
    @patch("custom_components.psegli.get_fresh_cookies", new_callable=AsyncMock)
    @patch("custom_components.psegli.check_addon_health", new_callable=AsyncMock)
    async def test_refresh_exception_records_unknown_failure_signals(
        self, mock_health, mock_fresh, mock_client_cls, mock_hass, mock_config_entry
    ):
        """Unexpected refresh exceptions record failed + unknown_runtime_error."""
        mock_health.return_value = True
        mock_fresh.side_effect = RuntimeError("addon exploded")
        mock_client = MagicMock()
        mock_client.test_connection = MagicMock(return_value=True)
        mock_client.cookie = "MM_SID=valid_test_cookie"
        mock_client_cls.return_value = mock_client
        mock_hass.config_entries.async_entries.return_value = [mock_config_entry]

        await async_setup_entry(mock_hass, mock_config_entry)
        handler = _get_registered_service_handler(mock_hass, "refresh_cookie")
        await handler(MagicMock(data={}))

        assert mock_hass.data[DOMAIN][_SIGNAL_LAST_REFRESH_RESULT] == "failed"
        assert (
            mock_hass.data[DOMAIN][_SIGNAL_LAST_REFRESH_FAILURE_CATEGORY]
            == CATEGORY_UNKNOWN_ERROR
        )

    @patch("custom_components.psegli.PSEGLIClient")
    @patch("custom_components.psegli.get_fresh_cookies", new_callable=AsyncMock)
    @patch("custom_components.psegli.check_addon_health", new_callable=AsyncMock)
    async def test_get_status_service_registered(
        self, mock_health, mock_fresh, mock_client_cls, mock_hass, mock_config_entry
    ):
        """get_status service is registered during setup."""
        mock_client = MagicMock()
        mock_client.test_connection = MagicMock(return_value=True)
        mock_client.cookie = "MM_SID=valid_test_cookie"
        mock_client_cls.return_value = mock_client

        await async_setup_entry(mock_hass, mock_config_entry)

        registered = [
            call[0][1]
            for call in mock_hass.services.async_register.call_args_list
        ]
        assert "get_status" in registered

    @patch("custom_components.psegli.PSEGLIClient")
    @patch("custom_components.psegli.get_fresh_cookies", new_callable=AsyncMock)
    @patch("custom_components.psegli.check_addon_health", new_callable=AsyncMock)
    async def test_get_status_returns_all_signal_keys(
        self, mock_health, mock_fresh, mock_client_cls, mock_hass, mock_config_entry
    ):
        """get_status handler returns all expected signal fields."""
        mock_client = MagicMock()
        mock_client.test_connection = MagicMock(return_value=True)
        mock_client.cookie = "MM_SID=valid_test_cookie"
        mock_client_cls.return_value = mock_client

        await async_setup_entry(mock_hass, mock_config_entry)

        # Extract the get_status handler
        handler = _get_registered_service_handler(mock_hass, "get_status")
        result = await handler(MagicMock(data={}))

        expected_keys = {
            "last_auth_probe_at",
            "last_auth_probe_result",
            "last_refresh_attempt_at",
            "last_refresh_reason",
            "last_refresh_result",
            "last_refresh_failure_category",
            "consecutive_auth_failures",
            "last_successful_update_at",
            "last_successful_datapoint_at",
            "cookie_age_seconds",
        }
        assert set(result.keys()) == expected_keys
        # Cookie age should be computed since we recorded it during setup
        assert result["cookie_age_seconds"] is not None
        assert result["consecutive_auth_failures"] == 0

    @patch("custom_components.psegli.PSEGLIClient")
    @patch("custom_components.psegli.get_fresh_cookies", new_callable=AsyncMock)
    @patch("custom_components.psegli.check_addon_health", new_callable=AsyncMock)
    async def test_refresh_logs_contain_attempt_id(
        self, mock_health, mock_fresh, mock_client_cls, mock_hass, mock_config_entry, caplog
    ):
        """Refresh log messages include the [refresh:XXXXXXXX] attempt ID."""
        mock_health.return_value = False
        mock_client = MagicMock()
        mock_client.test_connection = MagicMock(return_value=True)
        mock_client.cookie = "MM_SID=valid_test_cookie"
        mock_client_cls.return_value = mock_client
        mock_hass.config_entries.async_entries.return_value = [mock_config_entry]

        await async_setup_entry(mock_hass, mock_config_entry)
        handler = _get_registered_service_handler(mock_hass, "refresh_cookie")

        import logging
        with caplog.at_level(logging.INFO):
            await handler(MagicMock(data={}))

        refresh_logs = [r for r in caplog.records if "[refresh:" in r.message]
        assert len(refresh_logs) >= 1
        # Verify the attempt ID format: [refresh:XXXXXXXX]
        import re
        for record in refresh_logs:
            assert re.search(r"\[refresh:[0-9a-f]{8}\]", record.message)


class TestProcessChartDataSignals:
    """Tests for _process_chart_data signal tracking."""

    @patch("custom_components.psegli.get_last_cumulative_kwh", new_callable=AsyncMock)
    @patch("custom_components.psegli.async_add_external_statistics", new_callable=AsyncMock)
    async def test_records_last_successful_datapoint_at(
        self, mock_add_stats, mock_get_last_cumulative, mock_hass
    ):
        """_process_chart_data records max timestamp as last_successful_datapoint_at."""
        mock_get_last_cumulative.return_value = 0.0
        mock_hass.data.setdefault(DOMAIN, {})

        chart_data = {
            "Off-Peak Usage": {
                "valid_points": [
                    {"timestamp": datetime(2026, 3, 1, 5, 0, tzinfo=timezone.utc), "value": 1.0},
                    {"timestamp": datetime(2026, 3, 1, 6, 0, tzinfo=timezone.utc), "value": 2.0},
                ]
            }
        }

        await _process_chart_data(mock_hass, chart_data)

        assert _SIGNAL_LAST_SUCCESSFUL_DATAPOINT_AT in mock_hass.data[DOMAIN]
        recorded_at = mock_hass.data[DOMAIN][_SIGNAL_LAST_SUCCESSFUL_DATAPOINT_AT]
        assert recorded_at == datetime(2026, 3, 1, 6, 0, tzinfo=timezone.utc)

    @patch("custom_components.psegli.get_last_cumulative_kwh", new_callable=AsyncMock)
    @patch("custom_components.psegli.async_add_external_statistics", new_callable=AsyncMock)
    async def test_write_failure_suppresses_datapoint_signal(
        self, mock_add_stats, mock_get_last_cumulative, mock_hass
    ):
        """Write failure in async_add_external_statistics prevents datapoint signal update."""
        mock_get_last_cumulative.return_value = 0.0
        mock_add_stats.side_effect = Exception("recorder write failed")
        mock_hass.data.setdefault(DOMAIN, {})

        chart_data = {
            "Off-Peak Usage": {
                "valid_points": [
                    {"timestamp": datetime(2026, 3, 1, 5, 0, tzinfo=timezone.utc), "value": 1.0},
                ]
            }
        }

        await _process_chart_data(mock_hass, chart_data)

        assert _SIGNAL_LAST_SUCCESSFUL_DATAPOINT_AT not in mock_hass.data[DOMAIN]
