# AGENTS.md

Guidance for AI coding agents working in this repository.

## Project

PSEG Long Island energy usage integration for Home Assistant. Two components:

1. **Add-on** (`addons/psegli-automation/`) — FastAPI + Playwright browser automation. Logs in to `mysmartenergy.psegliny.com`, returns session cookies.
2. **Integration** (`custom_components/psegli/`) — HA custom component. Uses cookies to fetch energy data, writes HA long-term statistics.

## Commands

```bash
# Stable test lane (merge-gating): from repo root:
#   python3.12 -m venv .venv
#   .venv/bin/pip install --upgrade pip
#   .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q                      # run full test suite from repo root
.venv/bin/python -m pytest -q tests/test_init.py   # run a single test file
#
# Recent HA compatibility lane (informational):
#   python3.13.2+ required (HA 2026.2.0 needs >=3.13.2)
#   python3.13 -m venv .venv-ha-recent
#   .venv-ha-recent/bin/pip install --upgrade pip
#   .venv-ha-recent/bin/pip install -r requirements-dev-ha-recent.txt
#   .venv-ha-recent/bin/python -m pytest -q
# If Python 3.13.2+ is unavailable locally, rely on the ha-recent CI lane.
cd addons/psegli-automation && HEADED=1 python run.py  # add-on with visible browser
docker build -t psegli-automation addons/psegli-automation/
```

## Architecture

```
Config Flow → auto_login.py (aiohttp→add-on) → PSEGAutoLogin (Playwright) → mysmartenergy login → cookie

Scheduled (:00/:30):
  test_data_path() → valid? skip : re-login via add-on
  get_usage_data() → Dashboard/Chart/ChartData APIs → async_add_external_statistics
```

## Critical Patterns (Learned from Overhaul)

**Validate before persist.** Always call `test_connection()` before `async_update_entry()` with a new cookie. Bad cookies must never be saved to config entry.

**5xx ≠ auth failure.** `_get_dashboard_page()` raises `PSEGLIError` for 5xx (HA retries) and `InvalidAuth` for other non-200 (permanent disable). Conflating them causes incorrect integration disabling.

**`ConfigEntryAuthFailed` vs `ConfigEntryNotReady`.** Auth failures permanently disable the entry. Network errors trigger automatic retry. Never raise auth failure for transient issues.

**Sync client, async callers.** `PSEGLIClient` uses `requests` (synchronous). All callers use `hass.async_add_executor_job()`. Never call client methods directly from the event loop.

**Listener cleanup.** Playwright's `remove_listener()` may return sync or async. Use `inspect.isawaitable()` pattern (see `addons/psegli-automation/auto_login.py`).

**CAPTCHA sentinel contract.** Add-on defines `CAPTCHA_REQUIRED_SENTINEL` in `auto_login.py`. Integration defines `CAPTCHA_REQUIRED` in `auto_login.py`. Both must remain the string `"CAPTCHA_REQUIRED"`. Cross-referenced via comments.

**No sensor entities.** Integration creates only external statistics (`psegli:off_peak_usage`, `psegli:on_peak_usage`). Visible in Energy Dashboard, not Entities.

**Dynamic entry lookup.** Service handlers use `_get_active_entry(hass)` at call time — never close over a config entry reference.

**Cumulative statistics.** HA requires `has_sum=True` with running total. `get_last_cumulative_kwh()` finds the last known sum regardless of age.

**Timestamp quirk.** PSEG API returns Eastern local time as Unix epoch. `_parse_data()` interprets via `America/New_York` with pytz for DST handling.

**Cookie age tracking.** `_COOKIE_OBTAINED_AT` in `hass.data[DOMAIN]` stores UTC datetime. `_log_cookie_age()` logs age at scheduled check, expiry, and refresh events.

## Key Files

| File | Role |
|---|---|
| `custom_components/psegli/__init__.py` | Setup, services, scheduler, refresh, statistics |
| `custom_components/psegli/psegli.py` | `PSEGLIClient` — sync HTTP to PSEG API |
| `custom_components/psegli/auto_login.py` | Async client to add-on API |
| `custom_components/psegli/config_flow.py` | Config/options flow |
| `addons/psegli-automation/auto_login.py` | `PSEGAutoLogin` — Playwright login |
| `addons/psegli-automation/run.py` | FastAPI server |

## Testing Pitfalls

- Use `requirements-dev.txt` for deterministic runs and release gating.
- Use `requirements-dev-ha-recent.txt` for forward-compatibility checks only until
  HA 2026 harness migrations are complete.
- HA `ConfigFlow.unique_id` is read-only. Set `flow._unique_id` in test mocks.
- `mock_hass.async_add_executor_job` must actually run the sync function: `AsyncMock(side_effect=lambda f, *a: f(*a))`.
- Unload tests: "remaining loaded entries" checks `hass.data[DOMAIN]` keys, not `async_entries()` (which includes disabled/unloaded).
- Add-on `remove_listener` mock returns coroutine — use `inspect.isawaitable()` or tests emit RuntimeWarning.

## Error Handling

| Signal | Meaning | HA Behavior |
|---|---|---|
| `InvalidAuth` | Cookie rejected | `ConfigEntryAuthFailed` → permanent disable |
| `PSEGLIError` | Network/server error | `ConfigEntryNotReady` → auto retry |
| `CAPTCHA_REQUIRED` | reCAPTCHA triggered | User retries; persistent profile builds trust |

## Version

`VERSION` is the single source of truth for release version.

Use:

```bash
python3 scripts/sync_version.py --set <MAJOR.MINOR.PATCH[.HOTFIX]>
```

This updates all required hard-coded version fields (`manifest.json`,
addon config/build/runtime metadata, and repository metadata).

For the full release checklist (changelog handling, tag, GitHub release notes),
see [`docs/release-process.md`](docs/release-process.md).
