"""Tests for Home Assistant diagnostics export."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.psegli.const import (
    CONF_COOKIE,
    CONF_PASSWORD,
    CONF_USERNAME,
    DOMAIN,
)
from custom_components.psegli.diagnostics import async_get_config_entry_diagnostics


@pytest.fixture(autouse=True)
def mock_supervisor_clientsession():
    """Mock supervisor client session so diagnostics never create a real aiohttp session."""
    mock_resp = AsyncMock()
    mock_resp.status = 404
    mock_resp.json = AsyncMock(return_value={})
    mock_session = MagicMock()
    mock_session.get = MagicMock(
        return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_resp),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    with patch(
        "custom_components.psegli.supervisor.async_get_clientsession",
        return_value=mock_session,
    ):
        yield


@pytest.mark.asyncio
async def test_diagnostics_redacts_sensitive_config_entry_fields(
    mock_hass, mock_config_entry
):
    """Diagnostics payload redacts cookie and credential fields."""
    mock_hass.data[DOMAIN] = {}

    diagnostics = await async_get_config_entry_diagnostics(mock_hass, mock_config_entry)

    entry = diagnostics["config_entry"]
    assert entry["entry_id"] == mock_config_entry.entry_id
    assert entry["data"][CONF_COOKIE] == "**REDACTED**"
    assert entry["data"][CONF_PASSWORD] == "**REDACTED**"
    assert entry["data"][CONF_USERNAME] == "**REDACTED**"


@pytest.mark.asyncio
async def test_diagnostics_includes_current_signal_snapshot(mock_hass, mock_config_entry):
    """Diagnostics payload includes the same signal model as get_status."""
    now = datetime.now(tz=timezone.utc)
    mock_hass.data[DOMAIN] = {
        "_cookie_obtained_at": now - timedelta(seconds=90),
        "_last_auth_probe_result": "ok",
        "_last_refresh_result": "success",
        "_last_refresh_reason": "scheduled",
        "_consecutive_auth_failures": 2,
        "_last_successful_update_at": now,
    }

    diagnostics = await async_get_config_entry_diagnostics(mock_hass, mock_config_entry)

    signals = diagnostics["signals"]
    assert signals["last_auth_probe_result"] == "ok"
    assert signals["last_refresh_result"] == "success"
    assert signals["last_refresh_reason"] == "scheduled"
    assert signals["consecutive_auth_failures"] == 2
    assert signals["last_successful_update_at"] == now.isoformat()
    assert signals["cookie_age_seconds"] is not None
    assert signals["cookie_age_seconds"] >= 60


@pytest.mark.asyncio
async def test_diagnostics_include_artifact_summary_metadata_only(
    mock_hass, mock_config_entry
):
    """Diagnostics include add-on artifact summary without exposing content."""
    mock_hass.data[DOMAIN] = {}
    artifact_payload = {
        "count": 3,
        "items": [
            {
                "id": "1741286400000",
                "created_at": "2026-03-06T12:00:00+00:00",
                "category": "transient_site_error",
                "subreason": None,
                "html_file": "1741286400000/page.html",
                "screenshot_file": "1741286400000/page.png",
            }
        ],
    }

    with patch(
        "custom_components.psegli.diagnostics.get_addon_failure_artifacts",
        new_callable=AsyncMock,
        return_value=artifact_payload,
    ):
        diagnostics = await async_get_config_entry_diagnostics(
            mock_hass, mock_config_entry
        )

    signals = diagnostics["signals"]
    assert signals["artifact_count"] == 3
    assert signals["artifact_latest_created_at"] == "2026-03-06T12:00:00+00:00"
    assert signals["artifact_list_endpoint"].endswith("/artifacts/login-failures?limit=10")
    assert "html" not in diagnostics
    assert "screenshot" not in diagnostics


@pytest.mark.asyncio
async def test_diagnostics_artifact_latest_created_at_uses_parsed_timestamp_order(
    mock_hass, mock_config_entry
):
    """Latest artifact timestamp should be chosen by actual time, not string ordering."""
    mock_hass.data[DOMAIN] = {}
    artifact_payload = {
        "count": 2,
        "items": [
            {
                "id": "1",
                "created_at": "2026-03-06T12:00:00+00:00",
                "category": "unknown_runtime_error",
            },
            {
                "id": "2",
                "created_at": "2026-03-06T08:30:00-05:00",
                "category": "transient_site_error",
            },
        ],
    }

    with patch(
        "custom_components.psegli.diagnostics.get_addon_failure_artifacts",
        new_callable=AsyncMock,
        return_value=artifact_payload,
    ):
        diagnostics = await async_get_config_entry_diagnostics(
            mock_hass, mock_config_entry
        )

    assert diagnostics["signals"]["artifact_latest_created_at"] == "2026-03-06T08:30:00-05:00"


@pytest.mark.asyncio
async def test_diagnostics_artifact_summary_falls_back_cleanly_on_endpoint_error(
    mock_hass, mock_config_entry
):
    """Diagnostics keep the existing signal surface when the artifact endpoint fails."""
    mock_hass.data[DOMAIN] = {}

    with patch(
        "custom_components.psegli.diagnostics.get_addon_failure_artifacts",
        new_callable=AsyncMock,
        return_value=None,
    ):
        diagnostics = await async_get_config_entry_diagnostics(
            mock_hass, mock_config_entry
        )

    signals = diagnostics["signals"]
    assert signals["artifact_count"] == 0
    assert signals["artifact_latest_created_at"] is None
    assert signals["artifact_list_endpoint"].endswith("/artifacts/login-failures?limit=10")
