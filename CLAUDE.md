# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PSEG Long Island energy usage integration for Home Assistant. Two-component architecture:

1. **Add-on** (`addons/psegli-automation/`) — FastAPI microservice using Playwright to automate browser login to PSEG's website, handling MFA, and returning session cookies.
2. **Custom Integration** (`custom_components/psegli/`) — Standard HA integration that uses the add-on's cookies to fetch energy data from PSEG's mysmartenergy API and store it as HA long-term statistics.

## Development Commands

```bash
# Run the add-on server locally (port 8000)
cd addons/psegli-automation && python run.py

# Run with visible browser for MFA debugging
HEADED=1 python run.py

# Smoke test the running add-on
cd addons/psegli-automation && python test_addon.py

# Build the add-on Docker image
docker build -t psegli-automation addons/psegli-automation/

# Enable debug logging in Home Assistant (add to configuration.yaml)
logger:
  logs:
    custom_components.psegli: debug
```

No formal test suite, linter, or CI pipeline exists.

## Architecture Details

### Data Flow

```
HA Config Flow → auto_login.py (aiohttp) → Add-on /login → PSEGAutoLogin (Playwright)
  → Brave search → PSEG site → login form → MFA (if needed) → mysmartenergy redirect → cookie

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
| `/login` | POST | Login; returns cookies or `mfa_required=true` |
| `/login/mfa` | POST | Complete MFA with verification code |
| `/login-form` | POST | Form-data variant of `/login` |

### Two-Phase MFA Flow

`POST /login` may return `mfa_required=true` with the Playwright session held in a global `_mfa_session`. The user submits the code via `POST /login/mfa` (or the HA `enter_mfa_code` service), which completes the login and returns cookies.

### Key Code Conventions

- **No sensor entities** — the integration creates only external statistics (`psegli:off_peak_usage`, `psegli:on_peak_usage`), visible in the HA Energy Dashboard but not in the Entities list.
- **Cookie-based auth** — the raw browser cookie string is the sole auth mechanism, stored in `entry.data["cookie"]`.
- **Sync HTTP in async context** — `PSEGLIClient` uses `requests` (sync) wrapped via `ThreadPoolExecutor` / `run_in_executor`.
- **Stealth browser** — `PSEGAutoLogin` patches `navigator.webdriver`, spoofs fingerprints, and uses realistic mouse delays to avoid bot detection.
- **Cumulative statistics** — HA requires `has_sum=True` with a running total; `get_last_cumulative_kwh()` looks back up to 7 days for the last known sum.
- **Timestamp quirk** — PSEG API timestamps are shifted +4 hours in `_process_chart_data()` to align with actual Eastern peak hours.
- **Singleton scheduler guard** — `hass.data['global_scheduled_task_running']` prevents duplicate background refresh tasks on integration reload.
- **Persistent notifications** — used extensively to communicate auth status, MFA prompts, and errors to the user.

### Key Files

| File | Role |
|---|---|
| `custom_components/psegli/__init__.py` | Integration setup, coordinators, scheduled refresh, statistics storage |
| `custom_components/psegli/psegli.py` | `PSEGLIClient` — HTTP calls to PSEG mysmartenergy API (Dashboard, Chart, ChartData) |
| `custom_components/psegli/auto_login.py` | Async HTTP client to the add-on API |
| `custom_components/psegli/config_flow.py` | HA config/options flow (setup wizard, MFA step) |
| `addons/psegli-automation/auto_login.py` | `PSEGAutoLogin` — Playwright browser automation |
| `addons/psegli-automation/run.py` | FastAPI server entry point |

### HA Services

- `psegli.update_statistics` — manually trigger statistics fetch
- `psegli.refresh_cookie` — force cookie refresh via add-on
- `psegli.enter_mfa_code` — submit MFA code to complete login

## Version

Both `manifest.json` and add-on `config.yaml` share the version number (currently `2.4.5`). Update both when releasing.
