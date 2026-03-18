"""The PSEG Long Island integration."""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from collections.abc import Awaitable, Callable

import pytz

from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
)
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticMetaData
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

try:
    from homeassistant.core import SupportsResponse
    _SUPPORTS_RESPONSE_ONLY = SupportsResponse.ONLY
except ImportError:  # pragma: no cover - older HA versions
    _SUPPORTS_RESPONSE_ONLY = None
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    DOMAIN,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_COOKIE,
    CONF_ADDON_URL,
    CONF_DIAGNOSTIC_LEVEL,
    CONF_NOTIFICATION_LEVEL,
    DIAGNOSTIC_STANDARD,
    DIAGNOSTIC_VERBOSE,
    NOTIFICATION_CRITICAL_ONLY,
    NOTIFICATION_VERBOSE,
    DEFAULT_ADDON_URL,
    OPTION_ADDON_URL_AUTO,
    CONF_CAPTCHA_AUTO_RETRY_COUNT,
    CONF_CAPTCHA_AUTO_RETRY_DELAYS_MINUTES,
    DEFAULT_CAPTCHA_AUTO_RETRY_COUNT,
    DEFAULT_CAPTCHA_AUTO_RETRY_DELAYS_MINUTES,
    FIRST_START_GRACE_RETRIES,
    FIRST_START_GRACE_DELAY_SECONDS,
    CONF_PROACTIVE_REFRESH_MAX_AGE_HOURS,
    DEFAULT_PROACTIVE_REFRESH_MAX_AGE_HOURS,
    CONF_EXPIRY_WARNING_THRESHOLD_PERCENT,
    DEFAULT_EXPIRY_WARNING_THRESHOLD_PERCENT,
    DEFAULT_AUTO_BACKFILL_TRIGGER_HOURS,
    DEFAULT_MAX_AUTO_BACKFILL_DAYS,
)
from .psegli import InvalidAuth, PSEGLIClient, PSEGLIError
from .supervisor import async_get_addon_url_from_supervisor
from .auto_login import (
    get_fresh_cookies,
    check_addon_health,
    get_addon_profile_status,
    get_addon_failure_artifacts,
    CAPTCHA_REQUIRED,
    LoginResult,
    CATEGORY_CAPTCHA_REQUIRED,
    CATEGORY_ADDON_DISCONNECT,
    CATEGORY_ADDON_UNREACHABLE,
    CATEGORY_INVALID_CREDENTIALS,
    CATEGORY_TRANSIENT_SITE_ERROR,
    CATEGORY_UNKNOWN_ERROR,
)

_LOGGER = logging.getLogger(__name__)

# Key for storing cookie acquisition timestamp in hass.data[DOMAIN]
_COOKIE_OBTAINED_AT = "_cookie_obtained_at"
_AUTH_FAILURE_COUNT = "_consecutive_auth_failures"
_LAST_AUTH_LOOP_NOTIFICATION_AT = "_last_chart_auth_loop_notification_at"
_REFRESH_IN_PROGRESS_TASK = "_refresh_in_progress_task"
_PENDING_AUTH_REFRESH_TASK = "_pending_auth_refresh_task"
_CAPTCHA_RETRY_TASK = "_captcha_retry_task"
_STATISTICS_UPDATE_IN_PROGRESS_TASK = "_statistics_update_in_progress_task"
_STATISTICS_UPDATE_REQUEST = "_statistics_update_request"
_LAST_EXPIRY_WARNING_AT = "_last_expiry_warning_at"

_AUTH_FAILURE_THRESHOLD = 3
_AUTH_FAILURE_REFRESH_DELAY_SECONDS = 10
_AUTH_FAILURE_NOTIFICATION_COOLDOWN = timedelta(hours=24)

# Supervisor discovery cache
_SUPERVISOR_DISCOVERED_ADDON_URL = "_supervisor_discovered_addon_url"
_SUPERVISOR_DISCOVERED_ADDON_URL_AT = "_supervisor_discovered_addon_url_at"
_SUPERVISOR_DISCOVERY_TTL = timedelta(seconds=60)

# Add-on transport circuit breaker state
_ADDON_TRANSPORT_FAILURE_COUNT = "_addon_transport_failure_count"
_ADDON_CIRCUIT_OPEN_UNTIL = "_addon_circuit_open_until"
_ADDON_CIRCUIT_OPEN_FOR_URL = "_addon_circuit_open_for_url"
_ADDON_LAST_FAILURE_URL = "_addon_last_failure_url"
_LAST_ADDON_UNREACHABLE_NOTIFICATION_AT = "_last_addon_unreachable_notification_at"
_LAST_WORKING_ADDON_URL = "_last_working_addon_url"
_ADDON_CIRCUIT_OPEN_THRESHOLD = 3
_ADDON_CIRCUIT_OPEN_DURATION = timedelta(minutes=10)
_ADDON_UNREACHABLE_NOTIFICATION_COOLDOWN = timedelta(hours=24)
_ARTIFACT_LIST_LIMIT = 10

# Signal tracking keys (Phase 3.3)
_SIGNAL_LAST_AUTH_PROBE_AT = "_last_auth_probe_at"
_SIGNAL_LAST_AUTH_PROBE_RESULT = "_last_auth_probe_result"
_SIGNAL_LAST_REFRESH_ATTEMPT_AT = "_last_refresh_attempt_at"
_SIGNAL_LAST_REFRESH_REASON = "_last_refresh_reason"
_SIGNAL_LAST_REFRESH_RESULT = "_last_refresh_result"
_SIGNAL_LAST_REFRESH_FAILURE_CATEGORY = "_last_refresh_failure_category"
_SIGNAL_LAST_SUCCESSFUL_UPDATE_AT = "_last_successful_update_at"
_SIGNAL_LAST_SUCCESSFUL_DATAPOINT_AT = "_last_successful_datapoint_at"

# Home Assistant statistics metadata changed over time. Newer versions require
# explicit fields like mean_type/unit_class; older versions do not define them.
_STAT_METADATA_ANNOTATIONS = getattr(StatisticMetaData, "__annotations__", {})
_STAT_METADATA_SUPPORTS_MEAN_TYPE = "mean_type" in _STAT_METADATA_ANNOTATIONS
_STAT_METADATA_SUPPORTS_UNIT_CLASS = "unit_class" in _STAT_METADATA_ANNOTATIONS
try:
    from homeassistant.components.recorder.models import StatisticMeanType
    _MEAN_TYPE_NONE = StatisticMeanType.NONE
except Exception:  # pragma: no cover - older HA versions
    _MEAN_TYPE_NONE = 0
try:
    from homeassistant.components.recorder.models import StatisticUnitClass
    _UNIT_CLASS_ENERGY = StatisticUnitClass.ENERGY
except Exception:  # pragma: no cover - older HA versions
    _UNIT_CLASS_ENERGY = "energy"


def _log_cookie_age(hass: HomeAssistant, label: str) -> None:
    """Log the age of the current cookie for lifetime monitoring."""
    obtained_at = hass.data.get(DOMAIN, {}).get(_COOKIE_OBTAINED_AT)
    if obtained_at:
        age = datetime.now(tz=timezone.utc) - obtained_at
        hours, remainder = divmod(int(age.total_seconds()), 3600)
        minutes = remainder // 60
        _LOGGER.info("Cookie age at %s: %dh %dm", label, hours, minutes)


def _record_cookie_obtained(hass: HomeAssistant) -> None:
    """Record the current time as when the cookie was obtained/refreshed."""
    hass.data.setdefault(DOMAIN, {})[_COOKIE_OBTAINED_AT] = datetime.now(tz=timezone.utc)


def _parse_retry_delays(raw_value: Any) -> list[int]:
    """Parse retry delays from options (comma string or list), fallback to defaults."""
    if isinstance(raw_value, str):
        parts = [part.strip() for part in raw_value.split(",")]
        parsed = [int(part) for part in parts if part]
        cleaned = [value for value in parsed if value >= 0]
        if cleaned:
            return cleaned
    elif isinstance(raw_value, (list, tuple)):
        cleaned = []
        for value in raw_value:
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                continue
            if parsed >= 0:
                cleaned.append(parsed)
        if cleaned:
            return cleaned
    return list(DEFAULT_CAPTCHA_AUTO_RETRY_DELAYS_MINUTES)


def _get_captcha_retry_policy(entry: ConfigEntry | None) -> tuple[int, list[int]]:
    """Return effective CAPTCHA auto-retry count/delays from entry options."""
    retry_count = DEFAULT_CAPTCHA_AUTO_RETRY_COUNT
    retry_delays: list[int] = list(DEFAULT_CAPTCHA_AUTO_RETRY_DELAYS_MINUTES)
    if entry:
        count_raw = entry.options.get(
            CONF_CAPTCHA_AUTO_RETRY_COUNT,
            DEFAULT_CAPTCHA_AUTO_RETRY_COUNT,
        )
        try:
            retry_count = max(0, int(count_raw))
        except (TypeError, ValueError):
            retry_count = DEFAULT_CAPTCHA_AUTO_RETRY_COUNT

        retry_delays = _parse_retry_delays(
            entry.options.get(
                CONF_CAPTCHA_AUTO_RETRY_DELAYS_MINUTES,
                DEFAULT_CAPTCHA_AUTO_RETRY_DELAYS_MINUTES,
            )
        )

    return retry_count, retry_delays


def _coerce_int_option(value: Any, default: int, minimum: int, maximum: int) -> int:
    """Coerce an option value to int with bounds and fallback."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _compute_incremental_days_back(
    domain_data: dict[str, Any],
    now: datetime | None = None,
    trigger_hours: int = DEFAULT_AUTO_BACKFILL_TRIGGER_HOURS,
    max_days: int = DEFAULT_MAX_AUTO_BACKFILL_DAYS,
) -> int:
    """Compute backfill days from last successful datapoint cursor."""
    if trigger_hours <= 0 or max_days <= 0:
        return 0

    last_datapoint = domain_data.get(_SIGNAL_LAST_SUCCESSFUL_DATAPOINT_AT)
    if not isinstance(last_datapoint, datetime):
        return 0
    if last_datapoint.tzinfo is None:
        return 0

    current = now or datetime.now(tz=timezone.utc)
    gap = current - last_datapoint
    if gap <= timedelta(hours=trigger_hours):
        return 0

    days = max(1, int(gap.total_seconds() // 86400))
    if gap.total_seconds() % 86400:
        days += 1
    return min(days, max_days)


def _is_task_pending(task: asyncio.Task | None) -> bool:
    """Return True if the task exists and has not finished."""
    return task is not None and not task.done()


def _get_status_signals(domain_data: dict[str, Any]) -> dict[str, Any]:
    """Build the signal snapshot payload shared by service + diagnostics."""

    def _iso(dt: datetime | None) -> str | None:
        return dt.isoformat() if dt else None

    obtained_at = domain_data.get(_COOKIE_OBTAINED_AT)
    cookie_age = None
    if obtained_at:
        cookie_age = int((datetime.now(tz=timezone.utc) - obtained_at).total_seconds())

    return {
        "last_auth_probe_at": _iso(domain_data.get(_SIGNAL_LAST_AUTH_PROBE_AT)),
        "last_auth_probe_result": domain_data.get(_SIGNAL_LAST_AUTH_PROBE_RESULT),
        "last_refresh_attempt_at": _iso(domain_data.get(_SIGNAL_LAST_REFRESH_ATTEMPT_AT)),
        "last_refresh_reason": domain_data.get(_SIGNAL_LAST_REFRESH_REASON),
        "last_refresh_result": domain_data.get(_SIGNAL_LAST_REFRESH_RESULT),
        "last_refresh_failure_category": domain_data.get(
            _SIGNAL_LAST_REFRESH_FAILURE_CATEGORY
        ),
        "consecutive_auth_failures": domain_data.get(_AUTH_FAILURE_COUNT, 0),
        "last_successful_update_at": _iso(
            domain_data.get(_SIGNAL_LAST_SUCCESSFUL_UPDATE_AT)
        ),
        "last_successful_datapoint_at": _iso(
            domain_data.get(_SIGNAL_LAST_SUCCESSFUL_DATAPOINT_AT)
        ),
        "cookie_age_seconds": cookie_age,
        "captcha_retry_pending": _is_task_pending(domain_data.get(_CAPTCHA_RETRY_TASK)),
        "last_expiry_warning_at": _iso(domain_data.get(_LAST_EXPIRY_WARNING_AT)),
        "addon_transport_failure_count": domain_data.get(
            _ADDON_TRANSPORT_FAILURE_COUNT,
            0,
        ),
        "addon_circuit_open_until": _iso(domain_data.get(_ADDON_CIRCUIT_OPEN_UNTIL)),
        "last_working_addon_url": domain_data.get(_LAST_WORKING_ADDON_URL),
    }


def _build_artifact_list_endpoint(addon_url: str | None) -> str:
    """Return the metadata-only artifact listing endpoint URL."""
    base_url = (addon_url or DEFAULT_ADDON_URL).rstrip("/")
    return f"{base_url}/artifacts/login-failures?limit={_ARTIFACT_LIST_LIMIT}"


def _artifact_summary_defaults(addon_url: str | None) -> dict[str, Any]:
    """Return neutral artifact-summary fields."""
    return {
        "artifact_count": 0,
        "artifact_latest_created_at": None,
        "artifact_list_endpoint": _build_artifact_list_endpoint(addon_url),
    }


def _latest_artifact_created_at(payload: dict[str, Any]) -> str | None:
    """Return the latest created_at timestamp from an artifact listing payload."""
    items = payload.get("items")
    if not isinstance(items, list):
        return None

    def _parse(created_at: str) -> datetime | None:
        try:
            return datetime.fromisoformat(created_at)
        except ValueError:
            return None

    latest: str | None = None
    latest_dt: datetime | None = None
    for item in items:
        if not isinstance(item, dict):
            continue
        created_at = item.get("created_at")
        if not isinstance(created_at, str):
            continue
        created_at_dt = _parse(created_at)
        if created_at_dt is None:
            continue
        if latest_dt is None or created_at_dt > latest_dt:
            latest = created_at
            latest_dt = created_at_dt
    return latest


async def _build_status_snapshot(
    hass: HomeAssistant,
    entry: ConfigEntry | None,
    domain_data: dict[str, Any],
    *,
    artifact_fetcher: Callable[[str | None, int], Awaitable[dict | None]] | None = None,
) -> dict[str, Any]:
    """Build the shared async status snapshot for services + diagnostics."""
    snapshot = _get_status_signals(domain_data)
    configured_url = _get_configured_addon_url(entry)
    if artifact_fetcher is None:
        artifact_fetcher = get_addon_failure_artifacts

    try:
        addon_url = await _get_addon_url(hass, entry)
    except Exception as err:  # pragma: no cover - defensive
        _LOGGER.debug("Could not resolve addon URL for status snapshot: %s", err)
        addon_url = configured_url

    snapshot.update(_artifact_summary_defaults(addon_url))

    try:
        payload = await artifact_fetcher(addon_url, _ARTIFACT_LIST_LIMIT)
    except Exception as err:  # pragma: no cover - defensive
        _LOGGER.debug("Artifact summary unavailable for %s: %s", addon_url, err)
        payload = None

    if isinstance(payload, dict):
        count = payload.get("count", 0)
        try:
            artifact_count = max(0, int(count))
        except (TypeError, ValueError):
            artifact_count = 0
        snapshot["artifact_count"] = artifact_count
        snapshot["artifact_latest_created_at"] = _latest_artifact_created_at(payload)

    return snapshot


def _get_active_entry(hass: HomeAssistant) -> ConfigEntry | None:
    """Look up the first loaded config entry for this domain.

    Service handlers and scheduled tasks use this instead of closing over
    a specific entry, so they survive entry reloads without becoming stale.
    Only returns entries that have been fully set up (have data in hass.data).
    """
    domain_data = hass.data.get(DOMAIN, {})
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.entry_id in domain_data:
            return entry
    _LOGGER.warning("No loaded PSEG config entries found")
    return None


def _get_configured_addon_url(entry: ConfigEntry | None) -> str:
    """Return configured addon URL (options first, then entry data, then default)."""
    if entry:
        options_url = entry.options.get(CONF_ADDON_URL)
        if options_url:
            return str(options_url).rstrip("/")
        data_url = entry.data.get(CONF_ADDON_URL)
        if data_url:
            return str(data_url).rstrip("/")
    return DEFAULT_ADDON_URL.rstrip("/")


def _is_auto_managed_addon_url(entry: ConfigEntry | None) -> bool:
    """Return True when addon_url option is integration-managed discovery output."""
    return bool(entry and entry.options.get(OPTION_ADDON_URL_AUTO))


async def _get_cached_supervisor_addon_url(hass: HomeAssistant) -> str | None:
    """Return Supervisor-discovered add-on URL from TTL cache."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    now = datetime.now(tz=timezone.utc)
    cached_at = domain_data.get(_SUPERVISOR_DISCOVERED_ADDON_URL_AT)
    if (
        cached_at
        and isinstance(cached_at, datetime)
        and now - cached_at < _SUPERVISOR_DISCOVERY_TTL
    ):
        return domain_data.get(_SUPERVISOR_DISCOVERED_ADDON_URL)

    discovered = await async_get_addon_url_from_supervisor(hass)
    normalized = discovered.rstrip("/") if discovered else None
    domain_data[_SUPERVISOR_DISCOVERED_ADDON_URL] = normalized
    domain_data[_SUPERVISOR_DISCOVERED_ADDON_URL_AT] = now
    return normalized


async def _get_addon_url(hass: HomeAssistant, entry: ConfigEntry | None) -> str:
    """Resolve effective addon URL with Supervisor discovery fallback."""
    configured = _get_configured_addon_url(entry)
    default = DEFAULT_ADDON_URL.rstrip("/")
    if configured != default and not _is_auto_managed_addon_url(entry):
        return configured

    discovered = await _get_cached_supervisor_addon_url(hass)
    return discovered or configured


def _persist_discovered_addon_url(
    hass: HomeAssistant,
    entry: ConfigEntry | None,
    discovered_url: str | None,
    context: str,
) -> None:
    """Persist reachable addon URL in options when it differs from current setting."""
    if not discovered_url:
        return

    domain_data = hass.data.setdefault(DOMAIN, {})
    normalized = str(discovered_url).rstrip("/")
    domain_data[_LAST_WORKING_ADDON_URL] = normalized
    if not entry:
        return

    current = _get_configured_addon_url(entry)
    if normalized == current:
        return

    default = DEFAULT_ADDON_URL.rstrip("/")
    auto_managed = _is_auto_managed_addon_url(entry)
    # Do not overwrite explicit custom URLs that are non-default.
    if current != default and not auto_managed:
        _LOGGER.debug(
            "Keeping user-configured addon URL %s; discovered %s during %s",
            current,
            normalized,
            context,
        )
        return

    updated_options = {
        **entry.options,
        CONF_ADDON_URL: normalized,
        OPTION_ADDON_URL_AUTO: True,
    }
    hass.config_entries.async_update_entry(entry, options=updated_options)
    _LOGGER.info(
        "Updated addon URL from %s to %s based on successful %s probe",
        current,
        normalized,
        context,
    )


async def get_last_cumulative_kwh(hass: HomeAssistant, statistic_id: str) -> float:
    """Get the last recorded cumulative kWh for a given statistic_id.

    Uses get_last_statistics which returns the most recent entry regardless
    of age — no fixed lookback window, works even if offline for weeks.
    """
    try:
        last_stats = await get_instance(hass).async_add_executor_job(
            get_last_statistics, hass, 1, statistic_id, True, {"sum"}
        )
        if last_stats and statistic_id in last_stats:
            result = last_stats[statistic_id][0]["sum"]
            _LOGGER.debug("Last cumulative sum for %s: %.6f", statistic_id, result)
            return result
        _LOGGER.debug("No prior statistics for %s, starting from 0", statistic_id)
        return 0.0
    except Exception as e:
        _LOGGER.warning("Could not get last statistics for %s: %s, starting from 0", statistic_id, e)
        return 0.0

async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up the PSEG Long Island component."""
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up PSEG Long Island from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    domain_data = hass.data[DOMAIN]
    domain_data.setdefault(_AUTH_FAILURE_COUNT, 0)
    domain_data.setdefault(_ADDON_TRANSPORT_FAILURE_COUNT, 0)

    # Get credentials from config entry
    username = entry.data.get(CONF_USERNAME)
    password = entry.data.get(CONF_PASSWORD)
    cookie = entry.data.get(CONF_COOKIE, "")
    if not username or not password:
        _LOGGER.error("No username/password provided")
        return False

    # If no cookie available, try to get one from the addon
    if not cookie:
        addon_url = await _get_addon_url(hass, entry)
        _LOGGER.info(
            "No cookie available, attempting addon login via %s",
            addon_url,
        )
        total_attempts = 1 + FIRST_START_GRACE_RETRIES
        for attempt in range(1, total_attempts + 1):
            try:
                login_result = await get_fresh_cookies(
                    username,
                    password,
                    addon_url=addon_url,
                )
                if login_result.cookies:
                    _persist_discovered_addon_url(
                        hass,
                        entry,
                        login_result.addon_url,
                        "setup",
                    )

                if login_result.cookies:
                    cookie = login_result.cookies
                    _LOGGER.debug("Successfully obtained fresh cookies from addon")
                    break
                elif login_result.category == CATEGORY_CAPTCHA_REQUIRED:
                    _LOGGER.warning(
                        "reCAPTCHA challenge triggered during setup. "
                        "Try reloading the integration — it usually passes on retry."
                    )
                    await hass.services.async_call(
                        "persistent_notification",
                        "create",
                        {
                            "title": "PSEG Integration: reCAPTCHA Required",
                            "message": (
                                "reCAPTCHA challenge was triggered during login. "
                                "Try reloading the integration — it usually passes "
                                "after a few attempts with the persistent browser profile."
                            ),
                            "notification_id": "psegli_captcha_required",
                        },
                    )
                    break
                else:
                    _LOGGER.warning(
                        "Addon failed to get cookies (attempt %d/%d, category: %s, url: %s)",
                        attempt,
                        total_attempts,
                        login_result.category,
                        addon_url,
                    )
            except Exception as e:
                _LOGGER.warning(
                    "Failed to get cookies from addon (attempt %d/%d) url=%s: %s",
                    attempt,
                    total_attempts,
                    addon_url,
                    e,
                )

            if attempt < total_attempts:
                _LOGGER.info(
                    "Retrying addon login in %d seconds (attempt %d/%d)…",
                    FIRST_START_GRACE_DELAY_SECONDS,
                    attempt + 1,
                    total_attempts,
                )
                await asyncio.sleep(FIRST_START_GRACE_DELAY_SECONDS)

    if not cookie:
        _LOGGER.warning(
            "No cookie available and addon failed to provide one. "
            "Will mark entry not ready so Home Assistant retries setup."
        )
        await hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "title": "PSEG Integration: Cookie Required",
                "message": (
                    "No authentication cookie available. Ensure the PSEG addon "
                    "is running. Home Assistant will retry setup automatically. "
                    "If retries continue failing, re-add the integration and provide "
                    "a cookie manually during initial setup."
                ),
                "notification_id": "psegli_cookie_required",
            },
        )
        raise ConfigEntryNotReady(
            "No authentication cookie available; addon did not provide one"
        )

    # Create client and validate connection before storing
    client = PSEGLIClient(cookie)

    try:
        await hass.async_add_executor_job(client.test_connection)
        _LOGGER.debug("PSEG connection test successful")
    except InvalidAuth as e:
        _LOGGER.error("Authentication failed: %s", e)
        raise ConfigEntryAuthFailed("Invalid authentication")
    except PSEGLIError as e:
        _LOGGER.warning("Network error during setup, will retry: %s", e)
        raise ConfigEntryNotReady(f"PSEG unreachable: {e}") from e

    # Persist cookie and store client only after successful validation
    if cookie != entry.data.get(CONF_COOKIE, ""):
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_COOKIE: cookie},
        )
        _record_cookie_obtained(hass)
    else:
        # Existing cookie validated — record as baseline if not already tracked
        if _COOKIE_OBTAINED_AT not in hass.data.get(DOMAIN, {}):
            _record_cookie_obtained(hass)
    hass.data[DOMAIN][entry.entry_id] = client

    # Create coordinator for automatic updates (like Opower)
    coordinator = PSEGCoordinator(hass, entry, client)
    entry.runtime_data = coordinator

    # Listen for config changes (when user updates cookie via options)
    entry.async_on_unload(entry.add_update_listener(async_update_options))

    def _reset_auth_failure_counter(reason: str) -> None:
        """Reset consecutive auth-failure count after successful recovery."""
        previous = domain_data.get(_AUTH_FAILURE_COUNT, 0)
        if previous:
            _LOGGER.debug("Reset auth failure counter (%s): %d -> 0", reason, previous)
        domain_data[_AUTH_FAILURE_COUNT] = 0

    async def _record_auth_failure(reason: str) -> int:
        """Increment auth-failure count and emit loop notification on threshold."""
        count = domain_data.get(_AUTH_FAILURE_COUNT, 0) + 1
        domain_data[_AUTH_FAILURE_COUNT] = count
        _LOGGER.warning(
            "Auth failure recorded (%s): %d consecutive failures",
            reason,
            count,
        )

        if count < _AUTH_FAILURE_THRESHOLD:
            return count

        now = datetime.now(tz=timezone.utc)
        last_notified = domain_data.get(_LAST_AUTH_LOOP_NOTIFICATION_AT)
        if (
            last_notified is None
            or now - last_notified >= _AUTH_FAILURE_NOTIFICATION_COOLDOWN
        ):
            await hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": "PSEG Integration: Repeated Auth Failures",
                    "message": (
                        "PSEG authentication has failed repeatedly during data "
                        "updates. Integration will keep retrying automatically, "
                        "but please check addon health or provide a manual cookie."
                    ),
                    "notification_id": "psegli_chart_auth_failed_loop",
                },
            )
            domain_data[_LAST_AUTH_LOOP_NOTIFICATION_AT] = now

        return count

    def _log_verbose(msg: str, *args: Any) -> None:
        """Log at INFO level only when diagnostic_level is verbose."""
        active = _get_active_entry(hass)
        if active and active.options.get(CONF_DIAGNOSTIC_LEVEL) == DIAGNOSTIC_VERBOSE:
            _LOGGER.info(msg, *args)

    def _should_notify_verbose() -> bool:
        """Return True when notification_level is verbose."""
        active = _get_active_entry(hass)
        return bool(
            active
            and active.options.get(CONF_NOTIFICATION_LEVEL) == NOTIFICATION_VERBOSE
        )

    def _refresh_failure_message(category: str | None) -> str:
        """Return category-aware user guidance for cookie refresh failures."""
        if category == CATEGORY_TRANSIENT_SITE_ERROR:
            return (
                "PSEG appears temporarily unavailable. Please wait and retry. "
                "Your credentials and cookie are likely fine."
            )
        if category in (CATEGORY_ADDON_UNREACHABLE, CATEGORY_ADDON_DISCONNECT):
            return (
                "Could not reach the PSEG add-on. Please check add-on status and URL."
            )
        if category == CATEGORY_INVALID_CREDENTIALS:
            return (
                "Authentication was rejected. Please verify your credentials or provide a new cookie."
            )
        return (
            "Please check add-on status or provide a cookie manually."
        )

    def _parse_captcha_retry_reason(
        trigger_reason: str,
    ) -> tuple[bool, int | None, str | None]:
        """Return retry flag, attempt number, and origin for CAPTCHA retry reasons."""
        prefix = "captcha_auto_retry_"
        if not trigger_reason.startswith(prefix):
            return False, None, None

        suffix = trigger_reason[len(prefix):]
        attempt_raw, _, origin = suffix.partition(":")
        try:
            attempt = int(attempt_raw)
        except ValueError:
            attempt = None
        return True, attempt, origin or None

    def _format_captcha_retry_reason(trigger_reason: str) -> str:
        """Render CAPTCHA retry reasons with origin context for logs."""
        is_retry, attempt, origin = _parse_captcha_retry_reason(trigger_reason)
        if not is_retry:
            return trigger_reason
        attempt_label = attempt if attempt is not None else "?"
        if origin:
            return f"captcha_auto_retry_{attempt_label}:{origin}"
        return f"captcha_auto_retry_{attempt_label}"

    def _captcha_notification_message(trigger_reason: str) -> str:
        """Build user-facing CAPTCHA notification text."""
        is_retry, attempt, origin = _parse_captcha_retry_reason(trigger_reason)
        if is_retry:
            origin_text = f" triggered by {origin}" if origin else ""
            attempt_label = attempt if attempt is not None else "?"
            return (
                f"reCAPTCHA is still required during automatic retry {attempt_label}"
                f"{origin_text}. Automatic retries will continue while configured."
            )
        return (
            "reCAPTCHA challenge was triggered. Try refresh_cookie again — "
            "it usually passes after a few attempts."
        )

    def _record_signal(key: str, value: Any) -> None:
        """Store a signal value in domain_data."""
        domain_data[key] = value

    def _reset_addon_transport_state(reason: str) -> None:
        """Reset add-on transport failure count and circuit state."""
        failures = domain_data.get(_ADDON_TRANSPORT_FAILURE_COUNT, 0)
        open_until = domain_data.get(_ADDON_CIRCUIT_OPEN_UNTIL)
        open_for_url = domain_data.get(_ADDON_CIRCUIT_OPEN_FOR_URL)
        if failures or open_until:
            _LOGGER.info(
                "Reset add-on transport state (%s): failures=%d open_until=%s open_for_url=%s",
                reason,
                failures,
                open_until.isoformat() if isinstance(open_until, datetime) else open_until,
                open_for_url,
            )
        domain_data[_ADDON_TRANSPORT_FAILURE_COUNT] = 0
        domain_data.pop(_ADDON_CIRCUIT_OPEN_UNTIL, None)
        domain_data.pop(_ADDON_CIRCUIT_OPEN_FOR_URL, None)
        domain_data.pop(_ADDON_LAST_FAILURE_URL, None)
        # Allow fresh notification cycle after successful recovery/URL switch.
        domain_data.pop(_LAST_ADDON_UNREACHABLE_NOTIFICATION_AT, None)

    async def _maybe_notify_addon_unreachable(
        addon_url: str,
        trigger_reason: str,
    ) -> None:
        """Emit rate-limited notification for repeated add-on unreachability."""
        failures = domain_data.get(_ADDON_TRANSPORT_FAILURE_COUNT, 0)
        if failures < _ADDON_CIRCUIT_OPEN_THRESHOLD:
            return

        now = datetime.now(tz=timezone.utc)
        last_notified = domain_data.get(_LAST_ADDON_UNREACHABLE_NOTIFICATION_AT)
        if (
            isinstance(last_notified, datetime)
            and now - last_notified < _ADDON_UNREACHABLE_NOTIFICATION_COOLDOWN
        ):
            return

        open_until = domain_data.get(_ADDON_CIRCUIT_OPEN_UNTIL)
        next_probe = (
            open_until.isoformat() if isinstance(open_until, datetime) else "immediate"
        )
        last_working = domain_data.get(_LAST_WORKING_ADDON_URL)
        await hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "title": "PSEG Integration: Add-on Unreachable",
                "message": (
                    "PSEG add-on connectivity is failing repeatedly.\n\n"
                    f"Trigger: {trigger_reason}\n"
                    f"Active URL: {addon_url}\n"
                    f"Last known working URL: {last_working or 'unknown'}\n"
                    f"Transport failures: {failures}\n"
                    f"Next probe: {next_probe}"
                ),
                "notification_id": "psegli_addon_unreachable",
            },
        )
        domain_data[_LAST_ADDON_UNREACHABLE_NOTIFICATION_AT] = now

    async def _record_addon_transport_failure(
        category: str | None,
        addon_url: str,
        trigger_reason: str,
    ) -> int:
        """Increment transport failure count and open circuit at threshold."""
        last_failure_url = domain_data.get(_ADDON_LAST_FAILURE_URL)
        if isinstance(last_failure_url, str) and last_failure_url and last_failure_url != addon_url:
            _LOGGER.info(
                "Addon failure URL changed: %s -> %s (%s); resetting transport state",
                last_failure_url,
                addon_url,
                trigger_reason,
            )
            _reset_addon_transport_state("addon failure URL changed")

        count = domain_data.get(_ADDON_TRANSPORT_FAILURE_COUNT, 0) + 1
        domain_data[_ADDON_TRANSPORT_FAILURE_COUNT] = count
        domain_data[_ADDON_LAST_FAILURE_URL] = addon_url

        _LOGGER.warning(
            "Addon transport failure recorded (%s, category=%s): %d consecutive failures (url=%s)",
            trigger_reason,
            category,
            count,
            addon_url,
        )

        if count >= _ADDON_CIRCUIT_OPEN_THRESHOLD:
            proposed_open_until = datetime.now(tz=timezone.utc) + _ADDON_CIRCUIT_OPEN_DURATION
            existing_open_until = domain_data.get(_ADDON_CIRCUIT_OPEN_UNTIL)
            if not isinstance(existing_open_until, datetime) or existing_open_until < proposed_open_until:
                domain_data[_ADDON_CIRCUIT_OPEN_UNTIL] = proposed_open_until
            domain_data[_ADDON_CIRCUIT_OPEN_FOR_URL] = addon_url
            _LOGGER.warning(
                "Addon circuit opened after %d failures; suppressing probes until %s (url=%s)",
                count,
                domain_data[_ADDON_CIRCUIT_OPEN_UNTIL].isoformat(),
                addon_url,
            )

        await _maybe_notify_addon_unreachable(addon_url, trigger_reason)
        return count

    def _is_addon_circuit_open(trigger_reason: str, addon_url: str) -> bool:
        """Return True when add-on transport circuit is open and still cooling down."""
        open_until = domain_data.get(_ADDON_CIRCUIT_OPEN_UNTIL)
        if not isinstance(open_until, datetime):
            return False

        open_for_url = domain_data.get(_ADDON_CIRCUIT_OPEN_FOR_URL)
        if isinstance(open_for_url, str) and open_for_url and open_for_url != addon_url:
            _LOGGER.info(
                "Addon circuit reset due to URL change: was %s now %s (%s)",
                open_for_url,
                addon_url,
                trigger_reason,
            )
            _reset_addon_transport_state("addon URL changed")
            return False

        now = datetime.now(tz=timezone.utc)
        if now >= open_until:
            _LOGGER.info(
                "Addon circuit moving to half-open; retrying transport probe (%s, url=%s)",
                trigger_reason,
                addon_url,
            )
            domain_data.pop(_ADDON_CIRCUIT_OPEN_UNTIL, None)
            return False

        _LOGGER.warning(
            "Addon circuit open until %s; skipping transport probe (%s, url=%s)",
            open_until.isoformat(),
            trigger_reason,
            addon_url,
        )
        return True

    async def _refresh_cookie_once(
        trigger_reason: str,
        notify_on_success: bool,
        notify_on_failure: bool,
    ) -> bool:
        """Run one cookie refresh attempt and optional follow-up update."""
        attempt_id = uuid.uuid4().hex[:8]
        now = datetime.now(tz=timezone.utc)
        _record_signal(_SIGNAL_LAST_REFRESH_ATTEMPT_AT, now)
        _record_signal(_SIGNAL_LAST_REFRESH_REASON, trigger_reason)

        _LOGGER.info(
            "[refresh:%s] Starting cookie refresh (reason: %s)",
            attempt_id, trigger_reason,
        )

        active_entry = _get_active_entry(hass)
        if active_entry is None:
            _record_signal(_SIGNAL_LAST_REFRESH_RESULT, "failed")
            _record_signal(_SIGNAL_LAST_REFRESH_FAILURE_CATEGORY, None)
            return False

        username = active_entry.data.get(CONF_USERNAME)
        password = active_entry.data.get(CONF_PASSWORD)
        addon_url = await _get_addon_url(hass, active_entry)
        _LOGGER.info(
            "[refresh:%s] Using addon URL: %s",
            attempt_id,
            addon_url,
        )
        if not username or not password:
            _LOGGER.error(
                "[refresh:%s] No credentials available (%s)",
                attempt_id, trigger_reason,
            )
            _record_signal(_SIGNAL_LAST_REFRESH_RESULT, "failed")
            _record_signal(_SIGNAL_LAST_REFRESH_FAILURE_CATEGORY, None)
            return False

        if _is_addon_circuit_open(trigger_reason, addon_url):
            _record_signal(_SIGNAL_LAST_REFRESH_RESULT, "failed")
            _record_signal(
                _SIGNAL_LAST_REFRESH_FAILURE_CATEGORY,
                CATEGORY_ADDON_UNREACHABLE,
            )
            await _maybe_notify_addon_unreachable(addon_url, trigger_reason)
            return False

        if not await check_addon_health(addon_url):
            _LOGGER.warning(
                "[refresh:%s] Addon not available or unhealthy (%s, url=%s)",
                attempt_id,
                trigger_reason,
                addon_url,
            )
            await _record_addon_transport_failure(
                CATEGORY_ADDON_UNREACHABLE,
                addon_url,
                trigger_reason,
            )
            _record_signal(_SIGNAL_LAST_REFRESH_RESULT, "failed")
            _record_signal(
                _SIGNAL_LAST_REFRESH_FAILURE_CATEGORY,
                CATEGORY_ADDON_UNREACHABLE,
            )
            return False

        # Phase D: best-effort profile-status for warmup_state visibility
        profile_status = await get_addon_profile_status(addon_url)
        if profile_status and profile_status.get("warmup_state") != "ready":
            _LOGGER.info(
                "[refresh:%s] Addon profile warmup_state=%s (profile may still be building trust)",
                attempt_id,
                profile_status.get("warmup_state", "unknown"),
            )

        login_result = await get_fresh_cookies(
            username,
            password,
            addon_url=addon_url,
        )
        if login_result.cookies:
            _persist_discovered_addon_url(
                hass,
                active_entry,
                login_result.addon_url,
                f"refresh ({trigger_reason})",
            )
        failure_url = login_result.addon_url or addon_url
        if login_result.category == CATEGORY_CAPTCHA_REQUIRED:
            formatted_reason = _format_captcha_retry_reason(trigger_reason)
            is_retry_attempt, _, _ = _parse_captcha_retry_reason(trigger_reason)
            _reset_addon_transport_state("captcha response")
            _LOGGER.warning(
                "[refresh:%s] reCAPTCHA challenge triggered (%s, url=%s)",
                attempt_id,
                formatted_reason,
                failure_url,
            )
            _record_signal(_SIGNAL_LAST_REFRESH_RESULT, "failed")
            _record_signal(
                _SIGNAL_LAST_REFRESH_FAILURE_CATEGORY,
                CATEGORY_CAPTCHA_REQUIRED,
            )
            if not is_retry_attempt or _should_notify_verbose():
                await hass.services.async_call(
                    "persistent_notification",
                    "create",
                    {
                        "title": "PSEG Integration: reCAPTCHA Required",
                        "message": _captcha_notification_message(trigger_reason),
                        "notification_id": "psegli_captcha_required",
                    },
                )
            if is_retry_attempt:
                _LOGGER.info(
                    "[refresh:%s] CAPTCHA still required on %s; continuing current retry loop",
                    attempt_id,
                    formatted_reason,
                )
            else:
                await _schedule_captcha_retry(trigger_reason)
            return False

        if not login_result.cookies:
            _LOGGER.warning(
                "[refresh:%s] Addon failed to provide fresh cookies (%s, category: %s, url=%s)",
                attempt_id,
                trigger_reason,
                login_result.category,
                failure_url,
            )
            if login_result.category in (
                CATEGORY_ADDON_UNREACHABLE,
                CATEGORY_ADDON_DISCONNECT,
            ):
                await _record_addon_transport_failure(
                    login_result.category,
                    failure_url,
                    trigger_reason,
                )
            else:
                _reset_addon_transport_state("non-transport addon response")
            _record_signal(_SIGNAL_LAST_REFRESH_RESULT, "failed")
            _record_signal(
                _SIGNAL_LAST_REFRESH_FAILURE_CATEGORY,
                login_result.category,
            )
            if notify_on_failure or _should_notify_verbose():
                await hass.services.async_call(
                    "persistent_notification",
                    "create",
                    {
                        "title": "PSEG Integration: Cookie Refresh Failed",
                        "message": (
                            "Failed to refresh your PSEG authentication cookie "
                            f"(reason: {login_result.category}). "
                            f"{_refresh_failure_message(login_result.category)}"
                        ),
                        "notification_id": "psegli_cookie_refresh_failed",
                    },
                )
            return False

        cookies = login_result.cookies
        current_client = hass.data[DOMAIN][active_entry.entry_id]

        # Validate BEFORE persisting — rollback on failure
        old_cookie = current_client.cookie
        current_client.update_cookie(cookies)
        try:
            await hass.async_add_executor_job(current_client.test_connection)
        except Exception:
            current_client.update_cookie(old_cookie)
            raise
        _log_verbose(
            "[refresh:%s] New cookie validation successful (%s)",
            attempt_id, trigger_reason,
        )

        if hasattr(active_entry, "runtime_data") and active_entry.runtime_data:
            coord = active_entry.runtime_data
            if hasattr(coord, "client"):
                coord.client.update_cookie(cookies)

        hass.config_entries.async_update_entry(
            active_entry,
            data={**active_entry.data, CONF_COOKIE: cookies},
        )

        await _cancel_captcha_retry_task("cookie refresh success")
        _reset_addon_transport_state("cookie refresh success")
        _record_cookie_obtained(hass)
        _reset_auth_failure_counter("cookie refresh success")
        _record_signal(_SIGNAL_LAST_REFRESH_RESULT, "success")
        _record_signal(_SIGNAL_LAST_REFRESH_FAILURE_CATEGORY, None)
        _LOGGER.info(
            "[refresh:%s] Successfully refreshed cookie (%s)",
            attempt_id, trigger_reason,
        )

        # Fetch and save energy data with the new cookie.
        try:
            days_back = _compute_incremental_days_back(domain_data)
            if days_back > 0:
                _LOGGER.info(
                    "[refresh:%s] Running bounded backfill after refresh (days_back=%d)",
                    attempt_id,
                    days_back,
                )
            await _do_update_statistics(
                hass,
                days_back=days_back,
                trigger_refresh_on_auth_failure=False,
            )
        except Exception as stats_err:
            _LOGGER.warning(
                "[refresh:%s] Statistics update after refresh failed (%s): %s",
                attempt_id, trigger_reason, stats_err,
            )

        if notify_on_success or _should_notify_verbose():
            await hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": "PSEG Integration: Cookie Refreshed",
                    "message": "Successfully refreshed your PSEG authentication cookie.",
                    "notification_id": "psegli_cookie_refreshed",
                },
            )

        return True

    async def _refresh_cookie_shared(
        trigger_reason: str,
        notify_on_success: bool = False,
        notify_on_failure: bool = False,
    ) -> bool:
        """Single-flight wrapper so concurrent callers share one refresh task."""
        in_flight = domain_data.get(_REFRESH_IN_PROGRESS_TASK)
        current = asyncio.current_task()
        if in_flight and not in_flight.done():
            if in_flight is current:
                _LOGGER.debug(
                    "Refresh requested from active refresh task (%s); skipping",
                    trigger_reason,
                )
                return False
            _LOGGER.debug(
                "Refresh already in progress; waiting for result (%s)",
                trigger_reason,
            )
            return await in_flight

        task = asyncio.create_task(
            _refresh_cookie_once(
                trigger_reason=trigger_reason,
                notify_on_success=notify_on_success,
                notify_on_failure=notify_on_failure,
            )
        )
        domain_data[_REFRESH_IN_PROGRESS_TASK] = task
        try:
            return await task
        except Exception as err:
            _record_signal(_SIGNAL_LAST_REFRESH_RESULT, "failed")
            if not domain_data.get(_SIGNAL_LAST_REFRESH_FAILURE_CATEGORY):
                _record_signal(
                    _SIGNAL_LAST_REFRESH_FAILURE_CATEGORY,
                    CATEGORY_UNKNOWN_ERROR,
                )
            _LOGGER.error("Failed to refresh cookie (%s): %s", trigger_reason, err)
            if notify_on_failure:
                await hass.services.async_call(
                    "persistent_notification",
                    "create",
                    {
                        "title": "PSEG Integration: Cookie Refresh Error",
                        "message": f"Error refreshing your PSEG authentication cookie: {err}",
                        "notification_id": "psegli_cookie_refresh_error",
                    },
                )
            return False
        finally:
            if domain_data.get(_REFRESH_IN_PROGRESS_TASK) is task:
                domain_data.pop(_REFRESH_IN_PROGRESS_TASK, None)

    async def _schedule_auth_failure_refresh() -> None:
        """Coalesce immediate refresh triggers after update auth failures."""
        pending = domain_data.get(_PENDING_AUTH_REFRESH_TASK)
        if pending and not pending.done():
            _LOGGER.debug("Auth-failure refresh already scheduled")
            return

        async def _delayed_refresh() -> None:
            try:
                await asyncio.sleep(_AUTH_FAILURE_REFRESH_DELAY_SECONDS)
                await _refresh_cookie_shared(
                    trigger_reason="update_auth_failure",
                    notify_on_success=False,
                    notify_on_failure=True,
                )
            except asyncio.CancelledError:
                _LOGGER.debug("Auth-failure refresh task cancelled")
                raise
            finally:
                if domain_data.get(_PENDING_AUTH_REFRESH_TASK) is task:
                    domain_data.pop(_PENDING_AUTH_REFRESH_TASK, None)

        task = asyncio.create_task(_delayed_refresh())
        domain_data[_PENDING_AUTH_REFRESH_TASK] = task

    async def _cancel_captcha_retry_task(reason: str) -> None:
        """Cancel a pending CAPTCHA retry task, if one exists."""
        existing = domain_data.get(_CAPTCHA_RETRY_TASK)
        if not existing:
            domain_data[_CAPTCHA_RETRY_TASK] = None
            return
        if existing.done():
            domain_data[_CAPTCHA_RETRY_TASK] = None
            return
        if existing is asyncio.current_task():
            # Never await/cancel the currently running retry task.
            domain_data[_CAPTCHA_RETRY_TASK] = None
            return

        existing.cancel()
        try:
            await existing
        except asyncio.CancelledError:
            _LOGGER.debug("CAPTCHA retry task cancelled (%s)", reason)
        finally:
            if domain_data.get(_CAPTCHA_RETRY_TASK) is existing:
                domain_data[_CAPTCHA_RETRY_TASK] = None

    async def _schedule_captcha_retry(trigger_reason: str) -> None:
        """Schedule delayed CAPTCHA auto-retries after a CAPTCHA failure."""
        # Cancel any existing CAPTCHA retry task
        existing = domain_data.get(_CAPTCHA_RETRY_TASK)
        if existing and not existing.done():
            if existing is asyncio.current_task():
                _LOGGER.debug(
                    "CAPTCHA retry reschedule requested from active retry loop; keeping current task"
                )
                return
            await _cancel_captcha_retry_task(f"rescheduled by {trigger_reason}")

        active_entry = _get_active_entry(hass)
        retry_count, retry_delays = _get_captcha_retry_policy(active_entry)
        if retry_count <= 0:
            _LOGGER.info("CAPTCHA auto-retry disabled by options (count=0)")
            domain_data[_CAPTCHA_RETRY_TASK] = None
            return

        async def _captcha_retry_loop() -> None:
            try:
                for i in range(retry_count):
                    delay_min = retry_delays[i] if i < len(retry_delays) else retry_delays[-1]
                    _LOGGER.info(
                        "CAPTCHA auto-retry %d/%d scheduled in %d minutes (trigger: %s)",
                        i + 1, retry_count, delay_min, trigger_reason,
                    )
                    await asyncio.sleep(delay_min * 60)
                    retry_reason = f"captcha_auto_retry_{i + 1}:{trigger_reason}"
                    result = await _refresh_cookie_shared(
                        trigger_reason=retry_reason,
                        notify_on_success=True,
                        notify_on_failure=False,
                    )
                    if result:
                        _LOGGER.info("CAPTCHA auto-retry %d/%d succeeded", i + 1, retry_count)
                        return
                    # Stop if failure is no longer CAPTCHA-related
                    last_category = domain_data.get(_SIGNAL_LAST_REFRESH_FAILURE_CATEGORY)
                    if last_category != CATEGORY_CAPTCHA_REQUIRED:
                        _LOGGER.info(
                            "CAPTCHA auto-retry stopping: failure category changed to %s",
                            last_category,
                        )
                        return
                _LOGGER.warning("All %d CAPTCHA auto-retries exhausted", retry_count)
            except asyncio.CancelledError:
                _LOGGER.debug("CAPTCHA auto-retry task cancelled")
                raise
            finally:
                if domain_data.get(_CAPTCHA_RETRY_TASK) is retry_task:
                    domain_data[_CAPTCHA_RETRY_TASK] = None

        retry_task = asyncio.create_task(_captcha_retry_loop())
        domain_data[_CAPTCHA_RETRY_TASK] = retry_task

    # Business logic for updating statistics — called directly by service handler
    # and by internal callers (cookie refresh, scheduler) without the fake Call object.
    async def _do_update_statistics_once(
        hass_ref: HomeAssistant,
        days_back: int = 0,
        trigger_refresh_on_auth_failure: bool = True,
    ) -> bool:
        """Fetch PSEG data and update HA statistics."""
        _LOGGER.info("Statistics update started (days_back: %d)", days_back)

        active_entry = _get_active_entry(hass_ref)
        if active_entry is None:
            return False

        try:
            current_client = hass_ref.data[DOMAIN][active_entry.entry_id]

            _LOGGER.debug("Update using client with cookie (length=%d)", len(current_client.cookie))

            historical_data = await hass_ref.async_add_executor_job(
                current_client.get_usage_data, None, None, days_back
            )

            if "chart_data" in historical_data:
                await _process_chart_data(hass_ref, historical_data["chart_data"])
                _LOGGER.info("Statistics update completed successfully")
                _reset_auth_failure_counter("successful statistics update")
                _record_signal(
                    _SIGNAL_LAST_SUCCESSFUL_UPDATE_AT,
                    datetime.now(tz=timezone.utc),
                )
                return True
            else:
                _LOGGER.warning("No chart data found in response")
                return False

        except InvalidAuth as e:
            _LOGGER.error("Authentication failed during update: %s", e)
            await _record_auth_failure("update_auth_failure")
            if trigger_refresh_on_auth_failure:
                _LOGGER.info(
                    "Scheduling cookie refresh in %ds due to update auth failure",
                    _AUTH_FAILURE_REFRESH_DELAY_SECONDS,
                )
                await _schedule_auth_failure_refresh()
            return False

        except Exception as e:
            _LOGGER.error("Failed to update statistics: %s", e)
            return False

    async def _do_update_statistics(
        hass_ref: HomeAssistant,
        days_back: int = 0,
        trigger_refresh_on_auth_failure: bool = True,
    ) -> bool:
        """Single-flight wrapper so overlapping statistics updates share/coalesce work."""
        in_flight = domain_data.get(_STATISTICS_UPDATE_IN_PROGRESS_TASK)
        request_state = domain_data.get(_STATISTICS_UPDATE_REQUEST)
        current = asyncio.current_task()
        if in_flight and not in_flight.done():
            if in_flight is current:
                _LOGGER.debug(
                    "Statistics update requested from active update task; skipping"
                )
                return False
            if isinstance(request_state, dict):
                if request_state.get("started"):
                    request_state["rerun_days_back"] = max(
                        int(request_state.get("rerun_days_back", 0)),
                        days_back,
                    )
                    request_state["rerun_trigger_refresh_on_auth_failure"] = bool(
                        request_state.get("rerun_trigger_refresh_on_auth_failure", False)
                        or trigger_refresh_on_auth_failure
                    )
                else:
                    request_state["days_back"] = max(
                        int(request_state.get("days_back", 0)),
                        days_back,
                    )
                    request_state["trigger_refresh_on_auth_failure"] = bool(
                        request_state.get("trigger_refresh_on_auth_failure", False)
                        or trigger_refresh_on_auth_failure
                    )
            _LOGGER.debug(
                "Statistics update already in progress; waiting for result (days_back=%d)",
                days_back,
            )
            return await in_flight

        request_state = {
            "days_back": max(0, days_back),
            "trigger_refresh_on_auth_failure": trigger_refresh_on_auth_failure,
            "rerun_days_back": 0,
            "rerun_trigger_refresh_on_auth_failure": False,
            "started": False,
        }
        domain_data[_STATISTICS_UPDATE_REQUEST] = request_state

        async def _run_statistics_update() -> bool:
            while True:
                # Allow same-tick overlapping callers to coalesce onto the max days_back.
                await asyncio.sleep(0)
                request_state["started"] = True
                requested_days_back = int(request_state["days_back"])
                requested_trigger = bool(
                    request_state["trigger_refresh_on_auth_failure"]
                )
                result = await _do_update_statistics_once(
                    hass_ref,
                    days_back=requested_days_back,
                    trigger_refresh_on_auth_failure=requested_trigger,
                )
                rerun_days_back = int(request_state.get("rerun_days_back", 0))
                rerun_trigger = bool(
                    request_state.get("rerun_trigger_refresh_on_auth_failure", False)
                )
                if rerun_days_back <= 0 and not rerun_trigger:
                    return result
                request_state["days_back"] = rerun_days_back
                request_state["trigger_refresh_on_auth_failure"] = rerun_trigger
                request_state["rerun_days_back"] = 0
                request_state["rerun_trigger_refresh_on_auth_failure"] = False
                request_state["started"] = False

        task = asyncio.create_task(_run_statistics_update())
        domain_data[_STATISTICS_UPDATE_IN_PROGRESS_TASK] = task
        try:
            return await task
        finally:
            if domain_data.get(_STATISTICS_UPDATE_IN_PROGRESS_TASK) is task:
                domain_data.pop(_STATISTICS_UPDATE_IN_PROGRESS_TASK, None)
            if domain_data.get(_STATISTICS_UPDATE_REQUEST) is request_state:
                domain_data.pop(_STATISTICS_UPDATE_REQUEST, None)

    # Service handler delegates to business logic
    async def async_update_statistics_manual(call: Any) -> None:
        """Service handler for psegli.update_statistics."""
        days_back = call.data.get("days_back", 0)
        await _do_update_statistics(hass, days_back)

    # Register the manual service (guard against double-registration on reload)
    if not hass.services.has_service(DOMAIN, "update_statistics"):
        hass.services.async_register(
            DOMAIN,
            "update_statistics",
            async_update_statistics_manual
        )

    # Register the cookie refresh service
    async def async_refresh_cookie(call: Any) -> None:
        """Manually refresh the PSEG authentication cookie."""
        _LOGGER.debug("Cookie refresh service called")
        await _refresh_cookie_shared(
            trigger_reason="manual_service",
            notify_on_success=True,
            notify_on_failure=True,
        )

    if not hass.services.has_service(DOMAIN, "refresh_cookie"):
        hass.services.async_register(
            DOMAIN,
            "refresh_cookie",
            async_refresh_cookie
        )

    # Register the get_status service (Phase 3.3)
    async def async_get_status(call: Any) -> dict[str, Any]:
        """Return current integration status signals."""
        active_entry = _get_active_entry(hass)
        return await _build_status_snapshot(hass, active_entry, domain_data)

    if not hass.services.has_service(DOMAIN, "get_status"):
        register_kwargs: dict[str, Any] = {}
        if _SUPPORTS_RESPONSE_ONLY is not None:
            register_kwargs["supports_response"] = _SUPPORTS_RESPONSE_ONLY
        hass.services.async_register(
            DOMAIN,
            "get_status",
            async_get_status,
            **register_kwargs,
        )

    # Set up scheduled cookie refresh at XX:00 and XX:30
    async def async_scheduled_cookie_refresh() -> None:
        """Automatically refresh cookies at scheduled times.
        Only refreshes when the current cookie is invalid.
        """
        _LOGGER.debug("Scheduled cookie refresh triggered")

        active_entry = _get_active_entry(hass)
        if active_entry is None:
            return

        try:
            username = active_entry.data.get(CONF_USERNAME)
            password = active_entry.data.get(CONF_PASSWORD)
            cookie = active_entry.data.get(CONF_COOKIE, "")

            if not username or not password:
                _LOGGER.warning("No credentials available for scheduled cookie refresh")
                return

            # Proactive refresh: refresh before cookie is expected to expire
            max_age_hours = active_entry.options.get(
                CONF_PROACTIVE_REFRESH_MAX_AGE_HOURS,
                DEFAULT_PROACTIVE_REFRESH_MAX_AGE_HOURS,
            )
            if max_age_hours and max_age_hours > 0:
                obtained_at = domain_data.get(_COOKIE_OBTAINED_AT)
                if obtained_at:
                    cookie_age = datetime.now(tz=timezone.utc) - obtained_at
                    max_age = timedelta(hours=max_age_hours)
                    warning_threshold_percent = _coerce_int_option(
                        active_entry.options.get(
                            CONF_EXPIRY_WARNING_THRESHOLD_PERCENT,
                            DEFAULT_EXPIRY_WARNING_THRESHOLD_PERCENT,
                        ),
                        DEFAULT_EXPIRY_WARNING_THRESHOLD_PERCENT,
                        0,
                        100,
                    )

                    # Expiry warning: notify when approaching threshold
                    warning_age = max_age * warning_threshold_percent / 100
                    if (
                        warning_threshold_percent > 0
                        and cookie_age >= warning_age
                        and cookie_age < max_age
                    ):
                        now = datetime.now(tz=timezone.utc)
                        last_warning = domain_data.get(_LAST_EXPIRY_WARNING_AT)
                        if last_warning is None or now - last_warning >= timedelta(hours=4):
                            pct = int(cookie_age / max_age * 100)
                            remaining = max_age - cookie_age
                            _LOGGER.warning(
                                "Cookie age (%s) is %d%% of max lifetime (%sh); ~%s remaining",
                                cookie_age, pct, max_age_hours, remaining,
                            )
                            await hass.services.async_call(
                                "persistent_notification",
                                "create",
                                {
                                    "title": "PSEG Integration: Cookie Expiring Soon",
                                    "message": (
                                        f"Your PSEG authentication cookie is {pct}% "
                                        f"of its expected lifetime. Automatic refresh will be "
                                        f"attempted when it reaches {max_age_hours}h."
                                    ),
                                    "notification_id": "psegli_cookie_expiry_warning",
                                },
                            )
                            domain_data[_LAST_EXPIRY_WARNING_AT] = now

                    # Proactive refresh: cookie exceeded max age
                    if cookie_age >= max_age:
                        _LOGGER.info(
                            "Cookie age (%s) exceeds proactive threshold (%sh), refreshing",
                            cookie_age, max_age_hours,
                        )
                        proactive_ok = await _refresh_cookie_shared(
                            trigger_reason="proactive_age",
                            notify_on_success=False,
                            notify_on_failure=False,
                        )
                        if proactive_ok:
                            return
                        _LOGGER.debug(
                            "Proactive refresh failed; continuing with standard auth probe path"
                        )

            # If we have a cookie, test it first — skip refresh if still valid
            if cookie and active_entry.entry_id in hass.data.get(DOMAIN, {}):
                current_client = hass.data[DOMAIN][active_entry.entry_id]
                _record_signal(
                    _SIGNAL_LAST_AUTH_PROBE_AT,
                    datetime.now(tz=timezone.utc),
                )
                try:
                    await hass.async_add_executor_job(current_client.test_data_path)
                    _record_signal(_SIGNAL_LAST_AUTH_PROBE_RESULT, "ok")
                    _log_cookie_age(hass, "scheduled check (still valid)")
                    # Still update statistics — energy data may have new readings
                    try:
                        days_back = _compute_incremental_days_back(domain_data)
                        if days_back > 0:
                            _LOGGER.info(
                                "Incremental backfill triggered from datapoint gap (days_back=%d)",
                                days_back,
                            )
                        await _do_update_statistics(hass, days_back=days_back)
                        _log_verbose("Statistics updated (cookie still valid)")
                    except Exception as stats_err:
                        _LOGGER.warning("Statistics update failed: %s", stats_err)
                    return
                except InvalidAuth:
                    _record_signal(_SIGNAL_LAST_AUTH_PROBE_RESULT, "invalid_auth")
                    _log_cookie_age(hass, "cookie expired")
                    _LOGGER.info("Cookie expired, proceeding with refresh")
                except PSEGLIError:
                    _record_signal(_SIGNAL_LAST_AUTH_PROBE_RESULT, "transient_error")
                    _LOGGER.warning("Network error during cookie check, will attempt refresh")

            await _refresh_cookie_shared(
                trigger_reason="scheduled",
                notify_on_success=False,
                notify_on_failure=False,
            )

        except Exception as e:
            _LOGGER.error("Failed to refresh cookie during scheduled refresh: %s", e)

    # Use standard Home Assistant approach: refresh cookies at XX:00 and XX:30
    async def refresh_cookies_scheduled():
        """Refresh cookies at scheduled times (XX:00 and XX:30)."""
        try:
            while True:
                now = datetime.now(tz=timezone.utc)

                if now.minute < 30:
                    next_refresh = now.replace(minute=30, second=0, microsecond=0)
                else:
                    next_refresh = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)

                wait_seconds = max(0, (next_refresh - now).total_seconds())
                _LOGGER.debug("Next scheduled cookie refresh at %s (in %.0f seconds)",
                             next_refresh.strftime("%H:%M"), wait_seconds)

                await asyncio.sleep(wait_seconds)

                try:
                    await async_scheduled_cookie_refresh()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    _LOGGER.exception("Scheduled cookie refresh failed")
        except asyncio.CancelledError:
            _LOGGER.debug("Scheduled cookie refresh task cancelled cleanly")

    # Start the scheduled cookie refresh task AFTER all services are registered
    # Use a flag under hass.data[DOMAIN] to prevent multiple tasks across reloads
    if not hass.data[DOMAIN].get('_scheduled_task_running'):
        hass.data[DOMAIN]['_scheduled_task_running'] = True
        task = entry.async_create_background_task(
            hass,
            refresh_cookies_scheduled(),
            f"{DOMAIN}_scheduled_cookie_refresh",
        )
        hass.data[DOMAIN]['_scheduled_task'] = task
        _LOGGER.debug("Started scheduled cookie refresh task")
    else:
        _LOGGER.debug("Scheduled cookie refresh task already running, skipping duplicate")

    return True

class PSEGCoordinator(DataUpdateCoordinator):
    """Handle fetching PSEG data and updating statistics (like Opower)."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: PSEGLIClient):
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="PSEG",
            # No automatic updates - only manual and scheduled
            update_interval=None,
        )
        self.entry = entry
        self.client = client

async def _process_chart_data(hass: HomeAssistant, chart_data: dict[str, Any]) -> None:
    """Process chart data and update statistics."""
    local_tz = pytz.timezone('America/New_York')
    max_datapoint_at: datetime | None = None
    any_write_failed = False

    for series_name, series_data in chart_data.items():
        try:
            _LOGGER.debug("Series %s data type: %s", series_name, type(series_data))
            _LOGGER.debug("Series %s keys: %s", series_name, list(series_data.keys()) if isinstance(series_data, dict) else "not a dict")

            valid_points = series_data.get("valid_points", [])

            if not valid_points or not isinstance(valid_points, list):
                _LOGGER.warning("Valid points is not a list: %s", type(valid_points))
                continue

            # Determine which statistic this series maps to
            if "Off-Peak" in series_name:
                statistic_id = "psegli:off_peak_usage"
            elif "On-Peak" in series_name:
                statistic_id = "psegli:on_peak_usage"
            else:
                continue  # Skip non-peak series

            statistics = []

            # Check if this series has any meaningful data (non-zero values)
            non_zero_points = [point for point in valid_points if point.get("value", 0) > 0]
            if not non_zero_points:
                _LOGGER.debug("Skipping %s - all values are 0, no meaningful data", series_name)
                continue

            # Get the first timestamp to determine the hour
            first_timestamp = valid_points[0]["timestamp"] if valid_points else None
            if first_timestamp is None:
                _LOGGER.warning("No valid timestamp found for %s, skipping", series_name)
                continue

            # Convert to datetime if it's a timestamp
            if isinstance(first_timestamp, (int, float)):
                first_dt = datetime.fromtimestamp(first_timestamp, tz=timezone.utc)
            else:
                first_dt = first_timestamp

            # Ensure timezone awareness
            if first_dt.tzinfo is None:
                first_dt = local_tz.localize(first_dt)

            # Get the last cumulative sum before our first data point to ensure continuity
            _LOGGER.debug("Getting last cumulative sum for %s before %s", series_name, first_dt.strftime("%Y-%m-%d %H:%M"))
            cumulative_offset = await get_last_cumulative_kwh(hass, statistic_id)

            _LOGGER.debug("Starting statistics processing for %s with %d points, continuing from cumulative offset %.6f",
                         series_name, len(valid_points), cumulative_offset)

            points_processed = 0

            try:
                for i, point in enumerate(valid_points):
                    try:
                        # Extract timestamp and value from the point
                        if isinstance(point, dict) and "timestamp" in point and "value" in point:
                            timestamp = point["timestamp"]
                            value = point["value"]

                            # Convert timestamp to datetime if it's not already
                            if isinstance(timestamp, (int, float)):
                                timestamp = datetime.fromtimestamp(timestamp, tz=timezone.utc)

                            # Ensure we have a timezone-aware datetime
                            if timestamp.tzinfo is None:
                                timestamp = local_tz.localize(timestamp)

                            # Convert to UTC for HA
                            start_time = timestamp.astimezone(timezone.utc)

                            # Check for problematic values before conversion
                            if value is None:
                                _LOGGER.warning("Point %d: value is None, replacing with 0", i)
                                value = 0

                            if isinstance(value, str):
                                try:
                                    raw_energy_value = float(value)
                                except ValueError:
                                    _LOGGER.error("Point %d: cannot convert string value '%s' to float", i, value)
                                    continue
                            else:
                                raw_energy_value = float(value)

                            # Ensure energy value is non-negative
                            energy_value = max(0.0, raw_energy_value)

                            # Additional validation: check for unreasonably large values
                            if energy_value > 1000:  # More than 1000 kWh in an hour is suspicious
                                _LOGGER.warning("Point %d: suspiciously large energy value: %.6f kWh, capping at 100", i, energy_value)
                                energy_value = 100.0

                            # Calculate cumulative total
                            cumulative_kwh = energy_value + cumulative_offset
                            points_processed += 1

                            statistics.append({
                                "start": start_time,        # Time block start
                                "sum": cumulative_kwh,      # Cumulative total
                            })

                            # Track max datapoint timestamp for signals
                            if max_datapoint_at is None or start_time > max_datapoint_at:
                                max_datapoint_at = start_time

                            # Update cumulative_offset for the next point
                            cumulative_offset = cumulative_kwh

                        else:
                            _LOGGER.warning("Skipping invalid point %d: %s", i, point)
                            continue
                    except Exception as e:
                        _LOGGER.error("Error processing point %d (%s): %s", i, point, e)
                        continue

                _LOGGER.debug("Processed %d points for %s", points_processed, series_name)

            except Exception as e:
                _LOGGER.error("Error in enumerate loop for series %s: %s", series_name, e)
                continue

            # Use HA's Statistics API to update
            try:
                _LOGGER.debug("Calling async_add_external_statistics with %d statistics entries", len(statistics))
                if statistics:
                    _LOGGER.debug("First statistics entry: %s", statistics[0])
                    _LOGGER.debug("Last statistics entry: %s", statistics[-1])
                    _LOGGER.debug("Sample of statistics data being sent:")
                    for i, stat in enumerate(statistics[:3]):  # Show first 3 entries
                        _LOGGER.debug("  Entry %d: %s", i, stat)

                # Create metadata for the statistic
                metadata = {
                    "statistic_id": statistic_id,  # Use proper format
                    "source": "psegli",  # Use domain as source
                    "unit_of_measurement": "kWh",
                    "has_mean": False,
                    "has_sum": True,  # Set to True since we're sending cumulative totals
                    "name": f"PSEG {series_name}",
                }
                if _STAT_METADATA_SUPPORTS_MEAN_TYPE:
                    metadata["mean_type"] = _MEAN_TYPE_NONE
                if _STAT_METADATA_SUPPORTS_UNIT_CLASS:
                    metadata["unit_class"] = _UNIT_CLASS_ENERGY

                _LOGGER.debug("Using metadata: %s", metadata)

                # Check if the function is callable
                if not callable(async_add_external_statistics):
                    _LOGGER.error("async_add_external_statistics is not callable: %s", type(async_add_external_statistics))
                    continue

                result = async_add_external_statistics(
                    hass,
                    metadata,
                    statistics
                )

                # Check if result is awaitable
                if hasattr(result, '__await__'):
                    await result
                    _LOGGER.debug("Successfully updated statistics for %s", statistic_id)
                else:
                    _LOGGER.debug("Statistics update completed (non-awaitable result) for %s", statistic_id)

            except Exception as e:
                _LOGGER.error("Error calling async_add_external_statistics for %s: %s", statistic_id, e)
                any_write_failed = True
        except Exception as e:
            _LOGGER.error("Error processing series %s: %s", series_name, e)
            any_write_failed = True
            continue

    # Record the most recent datapoint timestamp only if all series
    # wrote successfully — avoids misleading diagnostics when a write
    # to the recorder fails partway through.
    if max_datapoint_at is not None and not any_write_failed:
        hass.data.setdefault(DOMAIN, {})[_SIGNAL_LAST_SUCCESSFUL_DATAPOINT_AT] = (
            max_datapoint_at
        )


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Update options for PSEG Long Island — apply cookie changes to live client."""
    new_cookie = entry.data.get(CONF_COOKIE, "")
    if new_cookie and entry.entry_id in hass.data.get(DOMAIN, {}):
        live_client = hass.data[DOMAIN][entry.entry_id]
        live_client.update_cookie(new_cookie)
        if hasattr(entry, 'runtime_data') and entry.runtime_data:
            coord = entry.runtime_data
            if hasattr(coord, 'client'):
                coord.client.update_cookie(new_cookie)
        domain_data = hass.data.get(DOMAIN, {})
        if domain_data.get(_AUTH_FAILURE_COUNT, 0):
            domain_data[_AUTH_FAILURE_COUNT] = 0
            _LOGGER.debug("Reset auth failure counter after manual cookie update")
        _LOGGER.debug("Applied updated cookie to live client")
    else:
        _LOGGER.debug("Options updated — no cookie change to apply")


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Unload the coordinator (guard against setup failure before runtime_data was set)
    if hasattr(entry, 'runtime_data') and entry.runtime_data:
        await entry.runtime_data.async_shutdown()

    # Clean up this entry's client from hass.data
    if DOMAIN in hass.data:
        hass.data[DOMAIN].pop(entry.entry_id, None)

    # Remaining loaded entries are those still present in hass.data[DOMAIN].
    domain_data = hass.data.get(DOMAIN, {})
    remaining_loaded_entries = [
        e for e in hass.config_entries.async_entries(DOMAIN) if e.entry_id in domain_data
    ]

    # Clean up scheduled task if this is the last instance
    if domain_data.get('_scheduled_task_running'):
        if not remaining_loaded_entries:
            task = domain_data.get('_scheduled_task')
            if task is not None:
                try:
                    if not task.done():
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass
                        _LOGGER.debug("Cancelled scheduled cookie refresh task")
                except Exception as e:
                    _LOGGER.warning("Error cancelling scheduled task: %s", e)
                domain_data.pop('_scheduled_task', None)

            domain_data.pop('_scheduled_task_running', None)
            _LOGGER.debug("Cleaned up scheduled task flag (last instance)")

            # Cancel any pending auth-failure refresh trigger task.
            pending_refresh = domain_data.get(_PENDING_AUTH_REFRESH_TASK)
            if pending_refresh is not None:
                try:
                    if not pending_refresh.done():
                        pending_refresh.cancel()
                        try:
                            await pending_refresh
                        except asyncio.CancelledError:
                            pass
                except Exception as e:
                    _LOGGER.warning("Error cancelling pending auth refresh task: %s", e)
                domain_data.pop(_PENDING_AUTH_REFRESH_TASK, None)

            # Cancel any CAPTCHA auto-retry task.
            captcha_retry = domain_data.get(_CAPTCHA_RETRY_TASK)
            if captcha_retry is not None:
                try:
                    if not captcha_retry.done():
                        captcha_retry.cancel()
                        try:
                            await captcha_retry
                        except asyncio.CancelledError:
                            pass
                except Exception as e:
                    _LOGGER.warning("Error cancelling CAPTCHA retry task: %s", e)
                domain_data[_CAPTCHA_RETRY_TASK] = None

            # Cancel any in-flight shared refresh task.
            in_flight_refresh = domain_data.get(_REFRESH_IN_PROGRESS_TASK)
            if in_flight_refresh is not None:
                try:
                    if not in_flight_refresh.done():
                        in_flight_refresh.cancel()
                        try:
                            await in_flight_refresh
                        except asyncio.CancelledError:
                            pass
                except Exception as e:
                    _LOGGER.warning("Error cancelling in-flight refresh task: %s", e)
                domain_data.pop(_REFRESH_IN_PROGRESS_TASK, None)

            # Cancel any in-flight shared statistics update task.
            in_flight_stats = domain_data.get(_STATISTICS_UPDATE_IN_PROGRESS_TASK)
            if in_flight_stats is not None:
                try:
                    if not in_flight_stats.done():
                        in_flight_stats.cancel()
                        try:
                            await in_flight_stats
                        except asyncio.CancelledError:
                            pass
                except Exception as e:
                    _LOGGER.warning("Error cancelling in-flight statistics task: %s", e)
                domain_data.pop(_STATISTICS_UPDATE_IN_PROGRESS_TASK, None)
                domain_data.pop(_STATISTICS_UPDATE_REQUEST, None)

            # Clear addon connectivity state for a clean next setup.
            domain_data.pop(_SUPERVISOR_DISCOVERED_ADDON_URL, None)
            domain_data.pop(_SUPERVISOR_DISCOVERED_ADDON_URL_AT, None)
            domain_data.pop(_ADDON_TRANSPORT_FAILURE_COUNT, None)
            domain_data.pop(_ADDON_CIRCUIT_OPEN_UNTIL, None)
            domain_data.pop(_ADDON_CIRCUIT_OPEN_FOR_URL, None)
            domain_data.pop(_ADDON_LAST_FAILURE_URL, None)
            domain_data.pop(_LAST_ADDON_UNREACHABLE_NOTIFICATION_AT, None)
            domain_data.pop(_LAST_WORKING_ADDON_URL, None)

    # Only remove services when the last entry is being unloaded
    if not remaining_loaded_entries:
        hass.services.async_remove(DOMAIN, "update_statistics")
        hass.services.async_remove(DOMAIN, "refresh_cookie")
        hass.services.async_remove(DOMAIN, "get_status")

    return True
