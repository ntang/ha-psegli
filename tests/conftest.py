"""Shared test fixtures for PSEG Long Island integration tests."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.psegli.const import DOMAIN, CONF_USERNAME, CONF_PASSWORD, CONF_COOKIE


@pytest.fixture
def mock_requests_session():
    """Mock requests.Session for PSEGLIClient tests."""
    session = MagicMock()
    # Use MagicMock for headers to allow .update() mocking
    session.headers = MagicMock()

    # Default: Dashboard returns 200, no redirect to login
    response = MagicMock()
    response.status_code = 200
    response.url = "https://mysmartenergy.psegliny.com/Dashboard"
    response.text = '<input name="__RequestVerificationToken" type="hidden" value="test_token_123" />'
    response.raise_for_status = MagicMock()
    session.get.return_value = response
    session.post.return_value = response
    return session


@pytest.fixture
def mock_aiohttp_session():
    """Mock aiohttp.ClientSession for addon client tests."""
    session = AsyncMock()
    return session


# ---------------------------------------------------------------------------
# Home Assistant core mocks for integration lifecycle tests
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_hass():
    """Minimal HomeAssistant mock for __init__.py tests."""
    hass = MagicMock()
    hass.data = {}

    # async_add_executor_job: run the sync function directly in-process
    async def _run_executor(func, *args):
        return func(*args)
    hass.async_add_executor_job = AsyncMock(side_effect=_run_executor)

    # Services
    hass.services = MagicMock()
    hass.services.has_service = MagicMock(return_value=False)
    hass.services.async_register = MagicMock()
    hass.services.async_remove = MagicMock()
    hass.services.async_call = AsyncMock()

    # Config entries
    hass.config_entries = MagicMock()
    hass.config_entries.async_entries = MagicMock(return_value=[])
    hass.config_entries.async_update_entry = MagicMock()

    # async_create_task: just schedule the coroutine and return a mock task
    hass.async_create_task = MagicMock(side_effect=lambda coro: _make_mock_task(coro))
    hass.async_create_background_task = MagicMock(
        side_effect=lambda coro, name, eager_start=True: _make_mock_task(coro)
    )

    return hass


def _make_mock_task(coro):
    """Create a mock task that cleans up the coroutine to avoid warnings."""
    coro.close()  # prevent "coroutine was never awaited"
    task = MagicMock()
    task.done.return_value = False
    task.cancel = MagicMock()
    return task


@pytest.fixture
def mock_config_entry():
    """Minimal ConfigEntry mock with valid credentials and cookie."""
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    entry.data = {
        CONF_USERNAME: "user@example.com",
        CONF_PASSWORD: "password123",
        CONF_COOKIE: "MM_SID=valid_test_cookie",
    }
    entry.async_on_unload = MagicMock()
    entry.add_update_listener = MagicMock(return_value=MagicMock())
    entry.async_create_background_task = MagicMock(
        side_effect=lambda hass, coro, name, eager_start=True: _make_mock_task(coro)
    )
    entry.runtime_data = None
    return entry


@pytest.fixture
def mock_config_entry_no_cookie():
    """ConfigEntry mock with credentials but no cookie."""
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    entry.data = {
        CONF_USERNAME: "user@example.com",
        CONF_PASSWORD: "password123",
        CONF_COOKIE: "",
    }
    entry.async_on_unload = MagicMock()
    entry.add_update_listener = MagicMock(return_value=MagicMock())
    entry.async_create_background_task = MagicMock(
        side_effect=lambda hass, coro, name, eager_start=True: _make_mock_task(coro)
    )
    entry.runtime_data = None
    return entry
