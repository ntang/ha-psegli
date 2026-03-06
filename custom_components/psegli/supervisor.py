"""Supervisor helpers for add-on URL discovery."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any
from urllib.parse import urlparse

import aiohttp
from homeassistant.core import HomeAssistant

from .const import ADDON_SLUG

_LOGGER = logging.getLogger(__name__)

_DEFAULT_SUPERVISOR_URL = "http://supervisor"
_SUPERVISOR_URL_ENV_KEYS = ("SUPERVISOR_URL", "HOMEASSISTANT_SUPERVISOR")
_SUPERVISOR_TOKEN_ENV_KEY = "SUPERVISOR_TOKEN"


def _get_supervisor_base_url() -> str:
    """Resolve Supervisor base URL from environment with fallback."""
    for key in _SUPERVISOR_URL_ENV_KEYS:
        value = os.environ.get(key)
        if value:
            return value.rstrip("/")
    return _DEFAULT_SUPERVISOR_URL


def _extract_addon_url(payload: dict[str, Any]) -> str | None:
    """Extract add-on base URL from Supervisor payload."""
    data = payload.get("data", payload)
    network = data.get("network", {}) if isinstance(data, dict) else {}
    host = network.get("host") or data.get("hostname")
    port = network.get("port") or data.get("port")

    # Some Supervisor payloads expose ports as a mapping (e.g. {"8000/tcp": 8000}).
    if not port and isinstance(network, dict):
        for key, value in network.items():
            if key in {"host", "hostname", "port"}:
                continue
            if isinstance(value, int):
                port = value
                break
            if isinstance(value, str):
                digits = "".join(ch for ch in value if ch.isdigit())
                if digits:
                    port = int(digits)
                    break
            if isinstance(key, str):
                digits = "".join(ch for ch in key if ch.isdigit())
                if digits:
                    port = int(digits)
                    break

    if not host:
        return None
    if isinstance(host, str) and host.startswith(("http://", "https://")):
        parsed = urlparse(host)
        if parsed.hostname:
            target_port = parsed.port or port
            if target_port:
                return f"{parsed.scheme}://{parsed.hostname}:{target_port}".rstrip("/")
            return f"{parsed.scheme}://{parsed.hostname}".rstrip("/")
        return host.rstrip("/")
    if port:
        return f"http://{host}:{port}".rstrip("/")
    return f"http://{host}".rstrip("/")


async def async_get_addon_url_from_supervisor(hass: HomeAssistant) -> str | None:
    """Discover add-on URL from Supervisor when available."""
    del hass  # placeholder for future HA-session usage

    base = _get_supervisor_base_url()
    url = f"{base}/addons/{ADDON_SLUG}/info"
    headers: dict[str, str] = {}
    token = os.environ.get(_SUPERVISOR_TOKEN_ENV_KEY)
    if token:
        headers["Authorization"] = f"Bearer {token}"

    timeout = aiohttp.ClientTimeout(total=3)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    _LOGGER.debug(
                        "Supervisor addon discovery returned non-200: status=%s url=%s",
                        resp.status,
                        url,
                    )
                    return None
                payload = await resp.json()
                discovered = _extract_addon_url(payload)
                if discovered:
                    _LOGGER.info("Discovered add-on URL from Supervisor: %s", discovered)
                return discovered
    except (aiohttp.ClientError, asyncio.TimeoutError) as err:
        _LOGGER.debug("Supervisor addon discovery unavailable: %s", err)
        return None
    except Exception as err:  # pragma: no cover - defensive
        _LOGGER.debug("Unexpected Supervisor discovery error: %s", err)
        return None
