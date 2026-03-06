"""Tests for Supervisor-based add-on URL discovery."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.psegli.supervisor import async_get_addon_url_from_supervisor


def _mock_client_session(response: AsyncMock):
    """Return a mocked aiohttp.ClientSession context manager."""
    session = AsyncMock()
    session.get = MagicMock(
        return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=response),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    return AsyncMock(
        __aenter__=AsyncMock(return_value=session),
        __aexit__=AsyncMock(return_value=False),
    )


@pytest.mark.asyncio
async def test_supervisor_discovery_returns_network_host_port():
    """Discovery returns addon URL from Supervisor network payload."""
    response = AsyncMock()
    response.status = 200
    response.json = AsyncMock(
        return_value={
            "data": {
                "network": {
                    "host": "84ee8c30-psegli-automation",
                    "port": 8000,
                }
            }
        }
    )

    with patch("custom_components.psegli.supervisor.aiohttp.ClientSession") as mock_cs:
        mock_cs.return_value = _mock_client_session(response)
        url = await async_get_addon_url_from_supervisor(MagicMock())

    assert url == "http://84ee8c30-psegli-automation:8000"


@pytest.mark.asyncio
async def test_supervisor_discovery_returns_none_on_non_200():
    """Discovery returns None when Supervisor responds non-200."""
    response = AsyncMock()
    response.status = 404
    response.json = AsyncMock(return_value={"message": "not found"})

    with patch("custom_components.psegli.supervisor.aiohttp.ClientSession") as mock_cs:
        mock_cs.return_value = _mock_client_session(response)
        url = await async_get_addon_url_from_supervisor(MagicMock())

    assert url is None


@pytest.mark.asyncio
async def test_supervisor_discovery_returns_none_on_timeout():
    """Discovery returns None when Supervisor call times out."""
    session = AsyncMock()
    session.get = MagicMock(side_effect=asyncio.TimeoutError())
    cm = AsyncMock(
        __aenter__=AsyncMock(return_value=session),
        __aexit__=AsyncMock(return_value=False),
    )

    with patch("custom_components.psegli.supervisor.aiohttp.ClientSession") as mock_cs:
        mock_cs.return_value = cm
        url = await async_get_addon_url_from_supervisor(MagicMock())

    assert url is None

