# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PSEG Long Island energy usage integration for Home Assistant. Two-component architecture:

1. **Add-on** (`addons/psegli-automation/`) — FastAPI microservice using Playwright to automate browser login to mysmartenergy.psegliny.com, handling reCAPTCHA via stealth + persistent browser profile, and returning session cookies.
2. **Custom Integration** (`custom_components/psegli/`) — Standard HA integration that uses the add-on's cookies to fetch energy data from PSEG's mysmartenergy API and store it as HA long-term statistics.

## Development Commands

```bash
# Run tests (Python 3.12 venv)
.venv/bin/python -m pytest tests/ -v

# Run a single test file
.venv/bin/python -m pytest tests/test_psegli_client.py -v

# Run the add-on server locally (port 8000)
cd addons/psegli-automation && python run.py

# Run with visible browser for debugging
HEADED=1 python run.py

# Build the add-on Docker image
docker build -t psegli-automation addons/psegli-automation/

# Enable debug logging in Home Assistant (add to configuration.yaml)
# logger:
#   logs:
#     custom_components.psegli: debug
```

## Architecture Details

### Data Flow

```
HA Config Flow → auto_login.py (aiohttp) → Add-on /login → PSEGAutoLogin (Playwright)
  → mysmartenergy.psegliny.com login form → reCAPTCHA (auto-resolves via stealth) → cookie

Every :00 and :30 (scheduled refresh):
  → test_connection() checks cookie validity
  → if expired: re-login via add-on
  → PSEGLIClient.get_usage_data() → PSEG Dashboard/Chart/ChartData APIs
  → _process_chart_data() → async_add_external_statistics (HA recorder)
```

### Add-on Endpoints

| Endpoint | Method | Purpose |
|---|---|---|
| `/health` | GET | Health check |
| `/login` | POST | Login; returns cookies or `captcha_required=true` |
| `/login-form` | POST | Form-data variant of `/login` |

### Key Code Conventions

- **No sensor entities** — the integration creates only external statistics (`psegli:off_peak_usage`, `psegli:on_peak_usage`), visible in the HA Energy Dashboard but not in the Entities list.
- **Cookie-based auth** — the raw browser cookie string (MM_SID + __RequestVerificationToken) is the sole auth mechanism, stored in `entry.data["cookie"]`.
- **Sync HTTP client** — `PSEGLIClient` uses `requests` (synchronous). Callers in `__init__.py` and `config_flow.py` use `hass.async_add_executor_job()` to run methods off the event loop.
- **Stealth browser** — `PSEGAutoLogin` uses `playwright-stealth` library (`Stealth` class + `apply_stealth_async()`) and `launch_persistent_context()` for reCAPTCHA trust.
- **Cumulative statistics** — HA requires `has_sum=True` with a running total; `get_last_cumulative_kwh()` uses `get_last_statistics` to find the last known sum regardless of age.
- **Timestamp quirk** — PSEG API returns timestamps as Eastern local time encoded as Unix epoch; `_parse_data()` interprets them as `America/New_York` via pytz for correct DST handling.
- **Dynamic entry lookup** — Service handlers and scheduled tasks use `_get_active_entry(hass)` to look up the config entry at call time, avoiding stale closure references on reload.
- **Persistent notifications** — used to communicate auth status, reCAPTCHA prompts, and errors to the user.

### Key Files

| File | Role |
|---|---|
| `custom_components/psegli/__init__.py` | Integration setup, services, scheduled refresh, statistics storage |
| `custom_components/psegli/psegli.py` | `PSEGLIClient` — synchronous HTTP calls to PSEG mysmartenergy API |
| `custom_components/psegli/auto_login.py` | Async HTTP client to the add-on API |
| `custom_components/psegli/config_flow.py` | HA config/options flow (setup wizard) |
| `addons/psegli-automation/auto_login.py` | `PSEGAutoLogin` — Playwright browser automation |
| `addons/psegli-automation/run.py` | FastAPI server entry point |
| `tests/` | 25 tests across 3 files (pytest + pytest-asyncio) |

### HA Services

- `psegli.update_statistics` — manually trigger statistics fetch (accepts `days_back` parameter)
- `psegli.refresh_cookie` — force cookie refresh via add-on

### Error Handling

- `InvalidAuth` — cookie rejected (redirect to login page). Triggers `ConfigEntryAuthFailed`.
- `PSEGLIError` — network error (timeout, DNS, connection refused). Should trigger `ConfigEntryNotReady` for retry.
- `CAPTCHA_REQUIRED` — reCAPTCHA challenge triggered. User should retry (persistent profile builds trust).

## Version

Both `manifest.json` and add-on `config.yaml` share the version number (currently `2.4.5`). Update both when releasing.
