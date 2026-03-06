"""Supervisor helpers for add-on URL discovery."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any
from urllib.parse import urlparse

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import ADDON_SLUG

_LOGGER = logging.getLogger(__name__)

_DEFAULT_SUPERVISOR_URL = "http://supervisor"
_SUPERVISOR_URL_ENV_KEYS = (
    "SUPERVISOR_URL",
    "HOMEASSISTANT_SUPERVISOR",
    "SUPERVISOR",
)
_SUPERVISOR_TOKEN_ENV_KEY = "SUPERVISOR_TOKEN"
_TCP_PORT_KEY_RE = re.compile(r"^(\d{1,5})/tcp$")
_WEBUI_PORT_RE = re.compile(r"\[PORT:(\d{1,5})\]")


def _get_supervisor_base_url() -> str:
    """Resolve Supervisor base URL from environment with fallback."""
    for key in _SUPERVISOR_URL_ENV_KEYS:
        value = os.environ.get(key)
        if value:
            normalized = value.rstrip("/")
            if normalized.startswith(("http://", "https://")):
                return normalized
            return f"http://{normalized}"
    return _DEFAULT_SUPERVISOR_URL


def _parse_port(raw: Any) -> int | None:
    """Convert a raw port-like value to an int if valid."""
    if isinstance(raw, int) and 1 <= raw <= 65535:
        return raw
    if isinstance(raw, str) and raw.isdigit():
        value = int(raw)
        if 1 <= value <= 65535:
            return value
    return None


def _extract_tcp_port_from_network_map(network: dict[str, Any]) -> int | None:
    """Extract a TCP port from Supervisor network mapping."""
    for key, value in network.items():
        if key in {"host", "hostname", "port"}:
            continue

        parsed_value = _parse_port(value)
        if parsed_value is not None:
            return parsed_value

        if isinstance(key, str):
            match = _TCP_PORT_KEY_RE.match(key.lower())
            if match:
                candidate = int(match.group(1))
                if 1 <= candidate <= 65535:
                    return candidate
    return None


def _extract_port_from_webui(webui: Any) -> int | None:
    """Extract explicit [PORT:<n>] placeholder from Supervisor webui template."""
    if not isinstance(webui, str):
        return None
    match = _WEBUI_PORT_RE.search(webui)
    if not match:
        return None
    candidate = int(match.group(1))
    if 1 <= candidate <= 65535:
        return candidate
    return None


def _extract_addon_url(payload: dict[str, Any]) -> str | None:
    """Extract add-on base URL from Supervisor payload."""
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        return None
    network_raw = data.get("network")
    network = network_raw if isinstance(network_raw, dict) else {}
    host = network.get("host") or data.get("hostname")

    port = (
        _parse_port(network.get("port"))
        or _parse_port(data.get("port"))
        or _parse_port(data.get("ingress_port"))
        or _extract_port_from_webui(data.get("webui"))
        or _extract_tcp_port_from_network_map(network)
    )

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
    base = _get_supervisor_base_url()
    url = f"{base}/addons/{ADDON_SLUG}/info"
    headers: dict[str, str] = {}
    token = os.environ.get(_SUPERVISOR_TOKEN_ENV_KEY)
    if token:
        headers["Authorization"] = f"Bearer {token}"

    timeout = aiohttp.ClientTimeout(total=3)
    session = async_get_clientsession(hass)
    try:
        async with session.get(url, headers=headers, timeout=timeout) as resp:
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
