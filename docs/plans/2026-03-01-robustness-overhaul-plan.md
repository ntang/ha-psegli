# PSEG Long Island Integration — Robustness Overhaul Plan

**Date:** 2026-03-01
**Goal:** Fix all identified bugs and fragility, rewrite the login flow to target mysmartenergy directly, add integration-focused tests to prevent regressions.

---

## Execution Status

| Phase | Status | Commit | Notes |
|-------|--------|--------|-------|
| 1 | **COMPLETE** | `fb0f070` | All 5 sub-tasks done. 596 insertions, 1508 deletions across 8 files. |
| 2 | **COMPLETE** | `395b51e` | psegli.py purely sync, all callers use async_add_executor_job |
| 3 | **COMPLETE** | `2f9e8f3` | get_last_statistics, CancelledError, service guards |
| 4 | **COMPLETE** | `732e5f9` | 25 tests across 3 files, all passing |
| 4.5 | **COMPLETE** | `c3f0fc3` | Codex review fixes: cookie logging, service lifecycle, docs |
| 4.6 | **COMPLETE** | `08a4c24` | Self-review: broken unload, validate-before-persist, unused imports |
| 4.7 | IN PROGRESS | — | Codex round 2: multi-entry guard, timestamps, addon URL, test-cookie, verification overhead |
| 5 | Pending | — | Post-deploy |

### Phase 4.6 — Combined Self-Review + Agent Review Findings

**High priority (fix now):**
- 4.6.1: CLAUDE.md is completely stale — still describes MFA, Brave search, ThreadPoolExecutor, 7-day lookback, no tests
- 4.6.2: `PSEGLIError` uncaught in `async_setup_entry` — should raise `ConfigEntryNotReady` for HA retry
- 4.6.3: Global scheduled task flags at `hass.data` root — move under `hass.data[DOMAIN]`
- 4.6.4: Cookie persisted BEFORE validation — `async_update_entry` called before `test_connection()`
- 4.6.5: Cookie value prefix shown in config_flow.py options UI (`[:50]`)

**Medium priority (fix now):**
- 4.6.6: Addon README response format shows cookies as JSON object (actual: string with MM_SID)
- 4.6.7: Redundant `if DOMAIN not in hass.data` guard (already set by `setdefault`)
- 4.6.8: Unused imports in `config_flow.py` (`load_yaml`, `HomeAssistantError`) and `__init__.py` (`callback`)
- 4.6.9: Unused `self._username`/`self._password` attrs in config_flow
- 4.6.10: `PSEGLIError` not caught distinctly in `config_flow.py` `test_connection` calls

**Low priority (future):**
- Fake `Call` object pattern (`type("Call", (), {"data": ...})()`) — fragile if handler accesses other attrs
- `_process_chart_data` has excessive defensive coding (callable check, post-write verification query)
- `await hass.async_create_task(hass.services.async_call(...))` double-wrapping — can just `await` directly
- `pytz` → `zoneinfo` migration (Python 3.9+ stdlib)
- +4h timestamp shift needs DST investigation before next DST change
- `run.py` test-cookie endpoint blocks event loop with sync `requests`
- Missing test coverage for `__init__.py` lifecycle and `config_flow.py`

### Phase 4.7 — Codex Review Round 2

**High priority (fix now):**
- 4.7.1: Multi-entry safety — config flow must enforce single instance (`async_get_options_flow` + `_abort_if_unique_id_configured`); `_get_active_entry` should guard against disabled entries
- 4.7.2: Timestamp handling — replace naive `fromtimestamp` + hardcoded +4h with proper UTC-aware `datetime.fromtimestamp(ts, tz=timezone.utc)` and explicit `America/New_York` localization

**Medium priority (fix now):**
- 4.7.3: Addon URL hardcoded — use `DEFAULT_ADDON_URL` from const.py in `auto_login.py` (integration-side)
- 4.7.4: `/test-cookie` endpoint — removed entirely (unused by integration; had blocking `requests.get` in async context and cookie in query string)
- 4.7.5: Verification query overhead — removed `statistics_during_period` verification query from `_process_chart_data` (debug overhead, extra recorder round-trip on every update)

**Medium priority (future):**
- 4.7.6: Additional test coverage — tests for `__init__.py` lifecycle (setup/unload/service handlers) and config flow

### Learnings & Adjustments

- **Phase 1.1-1.2 (add-on rewrite):** Completed in prior session. The add-on `auto_login.py` went from 937 lines to ~230 lines. The `run.py` lost its `/login/mfa` endpoint and `_mfa_session` singleton.
- **Phase 1.3-1.4 (integration side):** MFA references were spread across 6 files (auto_login.py, config_flow.py, __init__.py, const.py, services.yaml, translations). All removed in one pass. The `enter_mfa_code` service was also removed from `__init__.py` unload.
- **Phase 1 adjustment:** The plan listed 1.5 (harden cleanup/setup_browser) as a separate step, but it was already implemented as part of 1.1 — `cleanup()` nulls all refs, guards double-close, and `setup_browser()` calls `cleanup()` on failure. No separate step needed.
- **Phase 1 adjustment:** `config_flow.py` instance attrs `self._username`/`self._password` were added to `__init__()` but turned out to be unused — with MFA removed, there's no multi-step flow that needs to carry credentials between steps. The attrs are declared but harmless.
- **Phase 3 learnings:** The shadowed `local_tz` (3.4) was already gone from the Phase 1 `__init__.py` rewrite. The +4h timestamp shift (3.5) already has a TODO comment from Phase 2. `get_last_cumulative_kwh` went from ~70 lines (7-day lookback with manual timestamp parsing) to ~15 lines using `get_last_statistics`. Service registration now guards with `has_service()` and unload only removes services when the last config entry is being unloaded. The scheduled task loop now properly re-raises `CancelledError` to allow clean shutdown.
- **Phase 4 learnings:** Test infrastructure created with `pyproject.toml` (asyncio_mode=auto), 3 test files, 25 tests. Key mocking lessons: (1) `MagicMock` can't be awaited — use `AsyncMock` for anything in an `await` expression; (2) `session.headers = {}` fails because dict's `update` is read-only — use `MagicMock()` instead; (3) Testing Playwright requires careful `asyncio.sleep` patching since the login flow uses it for timing; (4) FastAPI endpoint tests use `httpx.ASGITransport` for in-process testing without a real server.
- **Phase 2 learnings:** Made `PSEGLIClient` purely synchronous — removed `test_connection` async wrapper and `get_usage_data` async wrapper (both used `ThreadPoolExecutor`). Renamed the old `_test_connection_sync` to just `test_connection` and `_get_usage_data_sync` to `get_usage_data`. All 7 callsites in `__init__.py` and 3 in `config_flow.py` updated to use `hass.async_add_executor_job`. Also added `REQUEST_TIMEOUT = 30` constant and `timeout=REQUEST_TIMEOUT` to all 4 `requests` calls. Separated network errors (`ConnectionError`/`Timeout` → `PSEGLIError`) from auth errors (redirect to login → `InvalidAuth`). The `re` module import was moved to top of file (was previously `import re` inside a function). Added the Phase 3.5 TODO comment on the +4h timestamp shift.

---

## Key Findings from Live Testing

Before writing this plan, we tested the login flow against the real PSEG infrastructure and discovered:

1. **mysmartenergy.psegliny.com has its own login form** — no need to route through myaccount/Okta. The form has fields `#LoginEmail`, `#LoginPassword`, `#RememberMe`, and a `.loginBtn` submit button.
2. **No MFA required** on the mysmartenergy login — just email, password, and a Google invisible reCAPTCHA.
3. **reCAPTCHA triggers a visible image challenge** on first visits, but **stops triggering after a few visits** when using `playwright-stealth` + a persistent browser profile (`.browser_profile/`).
4. **The `playwright-stealth` library is already a dependency but was never used** — the code hand-rolls inferior stealth patches. The library's `Stealth` class + `apply_stealth_async()` patches Canvas, WebGL, AudioContext, fonts, and dozens of other fingerprinting vectors.
5. **Cookies are session-only** (`expires=0`) — "Remember Me" doesn't change this. But the server-side session outlives the browser; cookies extracted from Playwright work with plain `requests` after the browser closes.
6. **Cookie lifetime is unknown** — needs monitoring. The current 30-minute refresh may be overly aggressive.
7. **The data API works** — `POST /Dashboard/Chart` + `GET /Dashboard/ChartData` return valid energy series data with the session cookie.
8. **The entire myaccount/Okta/MFA flow is unnecessary** — the original ~600 lines of Brave search → PSEG homepage → myaccount login → Okta MFA → redirect → mysmartenergy can be replaced with ~50 lines targeting mysmartenergy directly.

---

## Phase 1: Rewrite Login Flow (Direct mysmartenergy) — COMPLETE

*Replace the entire Brave → PSEG → myaccount → Okta → MFA chain with direct mysmartenergy login. This is the critical fix — it resolves the current timeout and eliminates the most fragile code.*

### 1.1 Rewrite `auto_login.py` — new `PSEGAutoLogin` class — DONE

**Files:** `addons/psegli-automation/auto_login.py`
**Issues resolved:** C1 (networkidle), H4 (blanket interception), M1 (mixed return types), M2 (route.continue), H5 (cookie logging)

Replace `simulate_realistic_browsing()` and its ~400 lines of multi-hop navigation with a direct flow:

1. Navigate to `https://mysmartenergy.psegliny.com/Dashboard` (shows login form when unauthenticated)
2. Fill `#LoginEmail`, `#LoginPassword`, check `#RememberMe`
3. Click `.loginBtn` (triggers invisible reCAPTCHA)
4. Listen for the `POST /Home/Login` AJAX response via `page.on("response", ...)`
5. On success: extract `MM_SID` + `__RequestVerificationToken` from `context.cookies()`
6. On CAPTCHA challenge: return a status indicating manual intervention needed

Key implementation details:
- Use `playwright-stealth` library (`Stealth` class + `apply_stealth_async()`) instead of hand-rolled patches
- Use `launch_persistent_context(user_data_dir=...)` for persistent browser profile — builds reCAPTCHA trust across sessions
- Use `page.on("response", ...)` for passive response capture (not `page.route("**/*", ...)` interception)
- No `networkidle` waits anywhere — use `domcontentloaded` + explicit element/response waits
- Introduce a `LoginResult` enum: `SUCCESS`, `FAILED`, `CAPTCHA_REQUIRED`
- Log cookie names and lengths only, never values

Remove entirely:
- Brave search navigation (Step 1)
- PSEG main site navigation + `#login` button click (Steps 2-3)
- All myaccount/Okta/MFA handling
- `continue_after_mfa()` method (no MFA on mysmartenergy)
- `setup_request_interception()` and `handle_request()` (replaced by passive response listener)
- Hand-rolled stealth patches in `setup_browser()` init script
- `exceptional_dashboard_data` tracking

### 1.2 Update `run.py` — simplify FastAPI endpoints — DONE

**Files:** `addons/psegli-automation/run.py`
**Issues resolved:** C2 (race condition), C3 (browser leak), H3 (MFA TTL)

Since mysmartenergy doesn't require MFA:
- Remove `/login/mfa` endpoint entirely
- Remove global `_mfa_session` singleton and all associated race conditions
- Simplify `/login` to: create `PSEGAutoLogin`, call `get_cookies()`, return result
- Add `asyncio.Lock` to prevent concurrent login attempts (simpler than the MFA lock — just one lock around the whole login)
- Add proper cleanup in all error paths

Keep `/login-form` as a convenience endpoint.

Add new endpoint:
- `GET /test-cookie` — accepts a cookie string, tests it against mysmartenergy Dashboard, returns valid/expired

### 1.3 Update integration's `auto_login.py` client — DONE

**Files:** `custom_components/psegli/auto_login.py`
**Issues resolved:** M7 (aiohttp timeout type)

- Remove `complete_mfa_login()` function and `MFA_REQUIRED` sentinel
- Remove MFA-related imports from `__init__.py`
- Fix `aiohttp` timeout to use `ClientTimeout(total=N)` instead of raw int
- Update `get_fresh_cookies()` return handling for new `LoginResult` enum
- Add `CAPTCHA_REQUIRED` as a possible return value (integration can notify user)

### 1.4 Update config flow — DONE

**Files:** `custom_components/psegli/config_flow.py`
**Issues resolved:** M5 (credentials in flow context)

- Remove MFA step (`async_step_mfa`) from both config flow and options flow
- Remove `mfa_method` from config schema (not needed for mysmartenergy)
- Move credentials from `self.context` to instance attributes (`self._username`, `self._password`)
- Handle `CAPTCHA_REQUIRED` response (show error asking user to try again — reCAPTCHA usually passes on retry with persistent profile)
- Also updated: `const.py` (removed `CONF_MFA_METHOD`), `services.yaml` (removed `enter_mfa_code`), `translations/en/config_flow.json` (replaced MFA errors with CAPTCHA error)

### 1.5 Harden `cleanup()` and `setup_browser()` — DONE (included in 1.1)

**Files:** `addons/psegli-automation/auto_login.py`
**Issues resolved:** H1, H2

- `cleanup()`: close context before browser, null out all references, guard double-close
- `setup_browser()`: call `cleanup()` in except block on partial failure

### Tests for Phase 1

- `test_direct_mysmartenergy_navigation`: Mock Playwright page, verify `goto` called with `mysmartenergy.psegliny.com/Dashboard`
- `test_no_brave_search_url`: Assert no reference to `search.brave.com` in new code
- `test_no_networkidle`: Grep for `networkidle` and assert zero matches
- `test_stealth_library_used`: Verify `Stealth` and `apply_stealth_async` are called
- `test_persistent_context_used`: Verify `launch_persistent_context` called (not `launch` + `new_context`)
- `test_login_success_returns_cookies`: Mock successful login response, verify cookie string returned
- `test_login_failure_returns_error`: Mock failed login, verify error returned
- `test_captcha_required_signaled`: Mock CAPTCHA scenario, verify `CAPTCHA_REQUIRED` returned
- `test_cleanup_idempotent`: Call `cleanup()` twice, no exception
- `test_concurrent_login_serialized`: Two concurrent `/login` calls, verify lock prevents overlap
- `test_cookie_values_not_logged`: Capture log output, verify no cookie values at INFO level
- `test_no_mfa_endpoint`: Verify `/login/mfa` returns 404
- `test_credentials_not_in_flow_context`: Inspect flow context after user step

---

## Phase 2: Integration Thread Safety and API Robustness — COMPLETE

*Fix the interconnected thread-safety cluster and HTTP robustness issues.*

### 2.1 Replace `ThreadPoolExecutor` with `hass.async_add_executor_job`

**Files:** `custom_components/psegli/psegli.py`, `custom_components/psegli/__init__.py`
**Issues:** C4, C5, C7

Remove the `async` wrappers (`get_usage_data`, `test_connection`) from `PSEGLIClient`. Make the class purely synchronous. Have all callers in `__init__.py` use:
```python
result = await hass.async_add_executor_job(client._test_connection_sync)
data = await hass.async_add_executor_job(client._get_usage_data_sync, None, None, days_back)
```

This eliminates:
- Per-call `ThreadPoolExecutor` creation (C5)
- Deprecated `asyncio.get_event_loop()` (C7)
- Thread-unsafe `Session` sharing (C4 — HA's executor serializes jobs)

### 2.2 Add timeouts to all `requests` calls

**Files:** `custom_components/psegli/psegli.py`
**Issues:** M6

Add `timeout=30` to every `self.session.get(...)` and `self.session.post(...)` call. Define a constant:
```python
REQUEST_TIMEOUT = 30  # seconds
```

### 2.3 Separate network errors from auth errors in `test_connection`

**Files:** `custom_components/psegli/psegli.py`
**Issues:** H8

Catch `ConnectionError` and `Timeout` separately from redirect-based auth failures. Raise `PSEGLIError` for network issues, `InvalidAuth` only for actual auth failures.

### 2.4 Fix `start_date`/`end_date` parameter handling

**Files:** `custom_components/psegli/psegli.py`
**Issues:** H6

Add a branch at the top of `_get_usage_data_sync`:
```python
if start_date is not None and end_date is not None:
    pass  # use caller-provided dates
elif days_back == 0:
    ...
```

### Tests for Phase 2

- `test_no_thread_pool_executor_usage`: Grep `psegli.py` for `ThreadPoolExecutor`; assert zero matches.
- `test_requests_have_timeouts`: Verify each `.get(` and `.post(` call has a `timeout` kwarg.
- `test_network_error_raises_psegli_error`: Mock `session.get` to raise `ConnectionError`; verify `PSEGLIError` raised (not `InvalidAuth`).
- `test_auth_failure_raises_invalid_auth`: Mock `session.get` returning redirect to login URL; verify `InvalidAuth` raised.
- `test_explicit_dates_respected`: Call `_get_usage_data_sync(start_date=X, end_date=Y)`; verify those dates used.

---

## Phase 3: Statistics and Scheduling Integrity — COMPLETE

*Fix data corruption risks and scheduling robustness.*

### 3.1 Replace 7-day lookback with `get_last_statistics`

**Files:** `custom_components/psegli/__init__.py`
**Issues:** C6

Replace the entire `get_last_cumulative_kwh()` function (~60 lines) with:
```python
async def get_last_cumulative_kwh(hass, statistic_id, before_timestamp):
    last_stats = await get_instance(hass).async_add_executor_job(
        get_last_statistics, hass, 1, statistic_id, True, {"sum"}
    )
    if last_stats and statistic_id in last_stats:
        return last_stats[statistic_id][0]["sum"]
    return 0.0
```

No more 7-day window. Works regardless of how long the integration was offline.

### 3.2 Add `CancelledError` handling to scheduled task

**Files:** `custom_components/psegli/__init__.py`
**Issues:** C8

Wrap the `while True` loop:
```python
async def refresh_cookies_scheduled():
    try:
        while True:
            await asyncio.sleep(wait_seconds)
            try:
                await async_scheduled_cookie_refresh()
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.exception("Scheduled refresh failed")
    except asyncio.CancelledError:
        _LOGGER.debug("Scheduled cookie refresh task cancelled cleanly")
```

### 3.3 Fix service registration lifecycle

**Files:** `custom_components/psegli/__init__.py`
**Issues:** H7

Guard service registration:
```python
if not hass.services.has_service(DOMAIN, "update_statistics"):
    hass.services.async_register(DOMAIN, "update_statistics", ...)
```

And in unload, only remove if no other entries remain:
```python
if not hass.config_entries.async_entries(DOMAIN):
    hass.services.async_remove(DOMAIN, "update_statistics")
```

### 3.4 Remove shadowed `local_tz` variable

**Files:** `custom_components/psegli/__init__.py`
**Issues:** M4

Delete the second `local_tz = pytz.timezone("America/New_York")` inside the loop and use the `local_tz` already computed at the top of `_process_chart_data`.

### 3.5 Investigate and document the +4h timestamp shift

**Files:** `custom_components/psegli/psegli.py`
**Issues:** M3

This needs investigation — the shift may be correct for one half of the year (EDT) and wrong for the other (EST). At minimum:
- Add a comment explaining the empirical basis
- Consider using `pytz` for proper Eastern time conversion if the API returns UTC
- If the shift is truly a fixed API quirk, document it clearly

### Tests for Phase 3

- `test_cumulative_sum_survives_long_offline`: Mock `get_last_statistics` to return data from 30 days ago; verify `get_last_cumulative_kwh` returns that sum (not 0.0).
- `test_cumulative_sum_returns_zero_when_no_history`: Mock `get_last_statistics` to return empty; verify 0.0 returned.
- `test_scheduled_task_cancels_cleanly`: Create the task, cancel it, verify no exception and log message appears.
- `test_services_not_double_registered`: Call `async_setup_entry` twice; verify services registered once.
- `test_services_removed_on_last_entry_unload`: Unload last entry; verify services removed.

---

## Phase 4: Test Infrastructure Setup

*Create the test scaffolding and run all tests. Built alongside Phases 1-3 but consolidated here for structure.*

### 4.1 Create test directory structure

```
tests/
  conftest.py              # Shared fixtures (mock hass, mock config entry, mock aiohttp)
  test_addon_endpoints.py  # Add-on: FastAPI endpoint tests with mocked PSEGAutoLogin
  test_addon_login.py      # Add-on: PSEGAutoLogin unit tests with mocked Playwright
  test_psegli_client.py    # Integration: PSEGLIClient tests with mocked requests
  test_init.py             # Integration: Setup, scheduling, statistics processing
  test_config_flow.py      # Integration: Config flow tests
```

### 4.2 Key fixtures

- `mock_hass` — Minimal HA core mock with `async_add_executor_job`, `services`, `config_entries`, `data`
- `mock_config_entry` — Config entry with test credentials and cookie
- `mock_addon_session` — `aiohttp.ClientSession` mock for add-on communication
- `mock_requests_session` — `requests.Session` mock for PSEG API calls
- `mock_playwright` — Minimal Playwright mock (page, context, browser) for add-on tests

### 4.3 Test runner

Add a minimal `pyproject.toml` (or `pytest.ini`) at the repo root:
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

Dependencies: `pytest`, `pytest-asyncio`, `pytest-aiohttp`, `aioresponses`

---

## Phase 5: Cookie Lifetime Monitoring (Post-Deploy)

*After deploying Phases 1-3, monitor how long the mysmartenergy session cookie stays valid to tune the refresh interval.*

### 5.1 Add cookie age tracking

Store the timestamp when a cookie was obtained. Log cookie age when testing validity. This lets us determine empirically whether the 30-minute refresh is needed or if we can extend to hours/days.

### 5.2 Tune refresh interval

Based on monitoring data, adjust `DEFAULT_SCAN_INTERVAL` and the scheduled refresh timing. If cookies last hours+, reduce refresh frequency to minimize unnecessary Playwright launches and reCAPTCHA exposure.

---

## Execution Order Summary

| Phase | Focus | Issues Fixed | Estimated Changes |
|-------|-------|-------------|-------------------|
| 1 | **Rewrite login flow** | C1, C2, C3, H1, H2, H3, H4, H5, M1, M2, M5, M7 | ~600 lines removed, ~150 added |
| 2 | **Integration thread safety** | C4, C5, C7, H6, H8, M6 | ~80 lines changed |
| 3 | **Statistics & scheduling** | C6, C8, H7, M3, M4 | ~90 lines changed |
| 4 | **Test infrastructure** | Prevention | ~400 lines new test code |
| 5 | **Cookie monitoring** | Optimization | ~20 lines added |

Phase 1 is the critical path — it fixes the current timeout, eliminates the most fragile code, and resolves 12 of 23 issues in one pass. Phases 2-3 are independent of each other and can be done in any order after Phase 1. Phase 4 tests are written alongside each phase.

## Test Utilities (for ongoing development)

Scripts in `addons/psegli-automation/` for manual testing:

| Script | Purpose |
|--------|---------|
| `test_direct_login_v2.py` | Headed browser login test — validates the full Playwright flow |
| `test_cookie_validity.py` | Tests a saved cookie against the API (reads from `.cookie` file) |
| `extract_and_test_cookie.py` | Extracts cookies from `.browser_profile/` and tests them |

Usage:
```bash
cd addons/psegli-automation
source .venv/bin/activate       # Python 3.12 venv
python test_direct_login_v2.py EMAIL PASSWORD   # logs in, saves cookie to .cookie
python test_cookie_validity.py                   # tests saved cookie
```
