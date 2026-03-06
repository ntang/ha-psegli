# PSEG Long Island Integration — Add-on Automation & Robustness Plan

**Date:** 2026-03  
**Goal:** Make the integration more automatic and more robust by adding add-on URL discovery, proactive refresh, CAPTCHA auto-retry, last-working-URL memory, add-on unreachable notifications, backfill after recovery, softer first-start behavior, and optional learned add-on URL.

---

## Context and Current State

- **Config flow:** User sets credentials and optional add-on URL (default `http://localhost:8000`). Options flow persists addon_url and observability settings even when cookie refresh fails (b5f558a).
- **Add-on URL probing:** Integration already probes multiple candidates (primary → default → fallback DNS names) on transport failure in `check_addon_health` and `get_fresh_cookies` (`custom_components/psegli/auto_login.py`).
- **Scheduler:** At :00 and :30, integration runs `test_data_path()`; if invalid auth, runs `_refresh_cookie_shared()`. No proactive refresh before expiry.
- **CAPTCHA:** When add-on returns CAPTCHA, integration notifies and returns; user must retry manually.
- **Signals:** `hass.data[DOMAIN]` holds cookie age, refresh result/category, auth probe result, etc. (Phase 3.3).
- **Statistics:** `_do_update_statistics(hass, days_back)` fetches chart data; no incremental window or automatic backfill today (Phase 4 in auth plan is separate).

This plan adds eight improvements that build on the above without duplicating the auth-refresh-stabilization work.

---

## Delivery Order and Dependencies

| Phase | Description | Depends on |
|-------|-------------|------------|
| 1 | Add-on URL discovery from Supervisor | — |
| 2 | Remember last-working add-on URL | — (benefits from 1) |
| 3 | Proactive cookie refresh before expiry | — |
| 4 | CAPTCHA automatic retry | — |
| 5 | Add-on unreachable notification | — |
| 6 | Backfill after recovery | Existing `_do_update_statistics`, `get_last_cumulative_kwh`, `last_successful_datapoint_at` (Phase 3.3) |
| 7 | Softer first-start when add-on may not be ready | — |
| 8 | Optional learned add-on URL (suggest/update) | 1, 2 |

Recommended implementation order: **1 → 2 → 3 → 4 → 5 → 7 → 6 → 8**. Phase 6 (backfill) is larger and can follow once 1–5 and 7 are stable. Phase 8 is a small UX enhancement after discovery and last-URL are in place.

---

## Phase 1 — Add-on URL Discovery from Supervisor

### Objective

When the integration runs on the same Home Assistant instance as the add-on, discover the add-on’s internal URL via the Supervisor API and use it as the primary (or only) addon URL when no URL is explicitly configured, so users do not need to look up or paste the add-on hostname.

### Implementation

**1.1 Supervisor API usage**

- Home Assistant Supervisor exposes a REST API. On supervised/OS installs, the integration can resolve add-on network info.
- **Mechanism:** Use `aiohttp` to call the Supervisor API. Typical base URL is provided by the `HOMEASSISTANT_SUPERVISOR` environment variable or the default `http://supervisor` (Supervisor container). Endpoint for add-on info: e.g. `GET /addons/{slug}/info` (slug: `psegli-automation` or the slug defined in addon `config.yaml`). Response includes network configuration (host, port). Build base URL as `http://<host>:<port>`.
- **Fallback:** If Supervisor is not available (core install, or API returns 404/5xx), or add-on is not installed, do not set discovered URL; use configured URL or `DEFAULT_ADDON_URL` as today.
- **Scope:** Discovery is used only when constructing the “effective” addon URL for health/login. It does not overwrite the user’s stored `addon_url` in options/data.

**1.2 Where to implement**

- **New module (recommended):** `custom_components/psegli/supervisor.py`
  - `async def get_addon_url_from_supervisor(hass: HomeAssistant) -> str | None`
  - Returns discovered base URL (no trailing slash) or `None` if unavailable.
  - Use `aiohttp` with a short timeout (e.g. 3s). Catch all exceptions and return `None`.
- **Integration with existing flow:**
  - In `_get_addon_url(entry)` in `__init__.py`: if entry has no options/data addon_url (or addon_url is still the default), call `get_addon_url_from_supervisor(hass)` once per call (or cache for a short TTL in `hass.data[DOMAIN]`, e.g. 60s) and use its result as the primary URL when non-None; otherwise keep current logic (options → data → default).
  - In `auto_login.py`, `check_addon_health` and `get_fresh_cookies` already receive the URL from callers; callers get that URL from `_get_addon_url`, so no change there once `_get_addon_url` returns the discovered URL when appropriate.
- **Config/options:** Do not add a new config option for “use Supervisor discovery” in the first version; when the stored addon_url is the default, use discovery. Optionally add a later option like `prefer_supervisor_discovery: bool` if we need to let users disable it.

**1.3 Constants and slug**

- Add-on slug must match the add-on’s `slug` in `addons/psegli-automation/config.yaml` (e.g. `psegli-automation`). Define in `const.py`: `ADDON_SLUG = "psegli-automation"`.

**1.4 Tests**

- `tests/test_supervisor.py` (new):
  - Mock Supervisor HTTP response (200 + JSON with network host/port); assert `get_addon_url_from_supervisor` returns expected base URL.
  - Mock 404 or 500; assert returns `None`.
  - Mock timeout or connection error; assert returns `None`.
- `tests/test_init.py` (or integration test):
  - With `_get_addon_url` and mocked `get_addon_url_from_supervisor`: when entry has no custom addon_url and Supervisor returns a URL, `_get_addon_url` returns that URL (or the cached value).
  - When entry has custom addon_url, discovery is not used (or is ignored) so stored URL wins.

**1.5 Risks and edge cases**

- **Supervisor not present:** Core or container installs may not have Supervisor; must always fall back cleanly.
- **Add-on not installed:** API may return 404; treat as “no discovery.”
- **Caching:** Short TTL (e.g. 60s) avoids hammering Supervisor; clear cache on integration reload if needed.

---

## Phase 2 — Remember Last-Working Add-on URL

### Objective

When a health check or login succeeds, store that URL and try it first on the next run, so we do not waste retries on localhost when the user’s real add-on is reachable via a fallback URL.

### Implementation

**2.1 Storage**

- Key in `hass.data[DOMAIN]`: `_last_working_addon_url: str | None`. Set when:
  - `check_addon_health(addon_url)` returns `True` (use the `base_url` that succeeded — currently we iterate candidates; we need to return or record which one succeeded), or
  - `get_fresh_cookies(..., addon_url=...)` returns `LoginResult(cookies=...)` (caller knows the URL it passed that succeeded).
- **Option A (simpler):** In `check_addon_health`, when a candidate succeeds, return that candidate URL (e.g. change return type to `tuple[bool, str | None]` or return the winning URL in a second way). Callers that care (e.g. scheduled refresh path) then store it in `hass.data[DOMAIN]["_last_working_addon_url"]`.
- **Option B:** Add a small helper `record_working_addon_url(hass, url)` and call it from:
  - `check_addon_health` (when it returns True, it must know which URL was used — so the loop in `check_addon_health` should return the winning base_url; change to `async def check_addon_health(...) -> tuple[bool, str | None]` or keep `-> bool` and add an optional out-parameter or a second function that returns last successful URL from the session; cleanest is `check_addon_health` returning `(True, base_url)` or `(False, None)`).
- **Recommended:** Change `check_addon_health` to return `tuple[bool, str | None]`: `(True, base_url)` on success, `(False, None)` on failure. In `__init__.py`, when result is True, call `domain_data["_last_working_addon_url"] = base_url`. For `get_fresh_cookies`, the caller (e.g. `_refresh_cookie_shared`) already has the URL it passed; on success, set `_last_working_addon_url` to that URL (the attempt_url that succeeded).

**2.2 Using the stored URL**

- In `_build_addon_url_candidates` (auto_login.py): if the integration has access to “last working URL,” prepend it to the candidate list (after primary, or as first fallback). Integration code does not have direct access to `hass` in `_build_addon_url_candidates`; that function only receives `addon_url`. So the “prefer last working” must happen at the caller: when building the list of URLs to try, the caller can pass in an optional `last_working_url` and `_build_addon_url_candidates(addon_url, last_working_url=None)` can prepend it (and dedupe). So:
  - Add optional parameter `last_working_url: Optional[str] = None` to `_build_addon_url_candidates`. If provided and not empty, insert it after primary (or as second element) and dedupe.
  - `check_addon_health` and `get_fresh_cookies` need to accept an optional `last_working_url` and pass it to `_build_addon_url_candidates`.
  - In `__init__.py`, when calling `check_addon_health(addon_url)` and `get_fresh_cookies(..., addon_url=addon_url)`, pass `last_working_url=domain_data.get("_last_working_addon_url")`.
- **Persistence:** Do not persist `_last_working_addon_url` to config entry; in-memory only. On restart we fall back to configured/default URL.

**2.3 Files to change**

- `custom_components/psegli/auto_login.py`: `_build_addon_url_candidates(addon_url, last_working_url=None)`, `check_addon_health(..., last_working_url=None)` return type `tuple[bool, str | None]` and return winning URL; `get_fresh_cookies(..., last_working_url=None)`.
- `custom_components/psegli/__init__.py`: pass `last_working_url` from `domain_data`; on health success set `_last_working_addon_url`; on refresh success set `_last_working_addon_url` to the addon_url that was used.

**2.4 Tests**

- `test_auto_login_integration.py`: When `last_working_url` is passed and differs from primary, assert it appears in candidates (e.g. second) and is tried (mock first URL fail, second succeed; verify second is the last_working_url).
- `test_init.py` or similar: After a successful `check_addon_health` or refresh, assert `hass.data[DOMAIN]["_last_working_addon_url"]` is set to the URL that succeeded.

**2.5 Risks**

- Stale last-working URL (e.g. add-on reinstalled with new hostname): we still have other candidates in the list, so we only “prefer” it; if it fails we try the rest.

---

## Phase 3 — Proactive Cookie Refresh Before Expiry

### Objective

Refresh the cookie on a schedule or when cookie age exceeds a threshold, instead of only when `test_data_path()` fails, to reduce “cookie just expired” failures during the next data fetch.

### Implementation

**3.1 Cookie age**

- We already have `_COOKIE_OBTAINED_AT` in `hass.data[DOMAIN]` (UTC datetime). Use it to compute age (e.g. `(now_utc - _COOKIE_OBTAINED_AT).total_seconds()`).

**3.2 Trigger policy**

- **Option A — Time-based:** Refresh cookie if it is older than `proactive_refresh_max_age_seconds` (default e.g. 20 hours = 72000). During scheduled run at :00/:30, before calling `test_data_path()`, check cookie age; if over threshold, skip probe and go straight to `_refresh_cookie_shared()`.
- **Option B — Fixed time:** Run a proactive refresh once per day at a fixed time (e.g. 03:00) in addition to :00/:30. That requires a second schedule or a check “if we haven’t refreshed in 24h, refresh now” at :00/:30.
- **Recommended:** Option A. In `async_scheduled_cookie_refresh()`:
  - After resolving `active_entry` and credentials, read `_COOKIE_OBTAINED_AT` from `domain_data`. If cookie age > `proactive_refresh_max_age_seconds`, skip `test_data_path()` and call `_refresh_cookie_shared(trigger_reason="proactive_age", ...)`.
  - If age is under threshold, keep current behavior: if cookie present, run `test_data_path()`; if invalid auth, refresh; else update statistics.

**3.3 Configuration**

- Add to options schema: `proactive_refresh_max_age_hours: float` (default `20.0`). Store in entry options; use in scheduler. If 0, disable proactive refresh (only refresh on auth failure).

**3.4 Constants and defaults**

- `const.py`: `CONF_PROACTIVE_REFRESH_MAX_AGE_HOURS = "proactive_refresh_max_age_hours"`, default `20.0`.
- In `__init__.py`, read from `entry.options.get(CONF_PROACTIVE_REFRESH_MAX_AGE_HOURS, 20.0)`.

**3.5 Tests**

- When cookie age > threshold, scheduled flow calls `_refresh_cookie_shared` with reason "proactive_age" and does not call `test_data_path` first.
- When cookie age < threshold, existing behavior: probe then refresh or update stats.
- When `proactive_refresh_max_age_hours` is 0, never trigger proactive refresh by age (only on auth failure).

**3.6 Risks**

- PSEG cookie TTL is unknown; 20h is conservative. Document that this is configurable.

---

## Phase 4 — CAPTCHA Automatic Retry

### Objective

When the add-on returns CAPTCHA, schedule one or more automatic retries after a delay (e.g. 5 and 15 minutes) so the integration can recover without the user having to run `refresh_cookie` manually.

### Implementation

**4.1 Scheduling**

- When `_refresh_cookie_shared` returns after a CAPTCHA result, do not only notify; also schedule a delayed retry.
- Use `hass.async_create_task(asyncio.sleep(delay); _refresh_cookie_shared(...))` or register a callback with `async_track_point_in_utc_time` for a single future retry.
- **Policy:** After CAPTCHA, schedule up to `captcha_auto_retry_count` retries (default 2), with delays `captcha_auto_retry_delays_minutes` (e.g. [5, 15]). If a retry succeeds, clear any further scheduled retries. If a retry again returns CAPTCHA, schedule the next delay; if it returns another failure category, stop auto-retry and keep existing notification behavior.
- Store in `hass.data[DOMAIN]`: `_captcha_retry_task: asyncio.Task | None` (or a list of scheduled callbacks) so we can cancel on unload and avoid duplicate schedules.

**4.2 Options**

- `CONF_CAPTCHA_AUTO_RETRY_COUNT` (int, default 2), `CONF_CAPTCHA_AUTO_RETRY_DELAYS_MINUTES` (list of ints, default [5, 15]). Validate in options flow (e.g. count ≤ 5, each delay 1–60).

**4.3 Unload**

- On `async_unload_entry`, cancel `_captcha_retry_task` if set.

**4.4 Tests**

- When refresh returns CAPTCHA, assert a task or timer is scheduled (e.g. mock `async_track_point_in_utc_time` or `asyncio.sleep` and fast-forward).
- When the delayed retry runs and succeeds, assert no further retry is scheduled and cookie is updated.
- When the delayed retry runs and gets CAPTCHA again, assert one more retry is scheduled (up to count).
- When unload runs, assert the retry task is cancelled.

**4.5 Risks**

- Do not hammer the site: cap count and use reasonable delays. Keep existing CAPTCHA notification so user is aware.

---

## Phase 5 — Add-on Unreachable Notification

### Objective

After repeated add-on unreachable / addon_disconnect outcomes over several scheduled cycles, show a clear persistent notification so the user knows to check add-on status and URL.

### Implementation

**5.1 Tracking**

- In `hass.data[DOMAIN]`: `_addon_unreachable_cycle_count: int`. Increment when scheduled refresh (or health check) fails with `CATEGORY_ADDON_UNREACHABLE` or `CATEGORY_ADDON_DISCONNECT`. Reset to 0 when health or login succeeds.
- Threshold: e.g. 3 consecutive cycles (configurable via `CONF_ADDON_UNREACHABLE_NOTIFY_AFTER_CYCLES`, default 3).

**5.2 Notification**

- When count reaches threshold, create persistent notification `psegli_addon_unreachable` with message like: “PSEG add-on has been unreachable for the last N refresh cycles. Check that the add-on is running and that the Automation Add-on URL in Integration Options matches the add-on’s address (e.g. from the add-on Info tab).”
- Cooldown: do not create the same notification again for 24h (store `_last_addon_unreachable_notification_at`); after 24h, if still unreachable, notify again.

**5.3 Where to increment/reset**

- In `_refresh_cookie_shared`, when we return False with category `CATEGORY_ADDON_UNREACHABLE` or `CATEGORY_ADDON_DISCONNECT`, increment `_addon_unreachable_cycle_count`. When we return True (success), set `_addon_unreachable_cycle_count = 0`.
- In scheduled flow, we call `_refresh_cookie_shared`; so the shared path is the right place. Optionally also increment when `check_addon_health` fails and we never get to refresh (so we count “scheduled cycle where add-on was unreachable”).
- When creating the notification, set `_last_addon_unreachable_notification_at = now` and only create if `now - _last_addon_unreachable_notification_at > 24h` (or if we never notified before).

**5.4 Tests**

- After 3 consecutive refresh failures with addon_disconnect, assert notification created.
- After one success, assert count reset and no notification (or next 3 failures create notification again after cooldown).
- Cooldown: assert we don’t create two notifications within 24h.

**5.5 Risks**

- Avoid alert storms: use cycle count and 24h cooldown.

---

## Phase 6 — Backfill After Recovery

### Objective

When the integration has been unable to fetch data for a period (invalid cookie, add-on down) and then recovers, automatically fetch and ingest data for the missed date range so the Energy dashboard has no gap.

### Implementation

**6.1 Gap detection**

- We have `_SIGNAL_LAST_SUCCESSFUL_DATAPOINT_AT` in `hass.data[DOMAIN]`. On startup, if missing, derive from recorder (max of last-written statistics timestamps) as in the auth plan Phase 4.
- Before routine `_do_update_statistics(hass, days_back=0)`, compute the gap: `now - last_successful_datapoint_at`. If gap > `auto_backfill_trigger_hours` (default 24), trigger a wider fetch.

**6.2 Bounded backfill**

- If gap > 24h: set `days_back = min(ceil(gap / 24), max_auto_backfill_days)` (e.g. `max_auto_backfill_days = 30`). Call `_do_update_statistics(hass, days_back=days_back)`.
- If gap > `max_auto_backfill_days` days: still fetch up to cap; optionally create a notification that the user may run manual `update_statistics` with larger `days_back` for older data.
- After successful backfill, update `last_successful_datapoint_at` and continue with normal incremental window on subsequent runs.

**6.3 Options**

- `CONF_AUTO_BACKFILL_TRIGGER_HOURS` (default 24), `CONF_MAX_AUTO_BACKFILL_DAYS` (default 30). Add to options schema and config flow.

**6.4 Overlap with auth plan Phase 4**

- If the auth refresh stabilization plan Phase 4 (incremental fetch + backfill) is implemented first, align with it: use the same `last_successful_datapoint_at` and same fetch-planning logic. This plan’s “backfill after recovery” is the same bounded backfill behavior; ensure we only add options and trigger conditions, not duplicate logic.

**6.5 Tests**

- When `last_successful_datapoint_at` is 48h ago, assert next scheduled run calls `_do_update_statistics` with `days_back` at least 2, and cap at 30.
- When gap is 60 days, assert `days_back=30` and optional notification.
- When gap is 12h, assert no backfill (routine fetch only).

**6.6 Risks**

- PSEG API may rate-limit or limit date range; document limits. Duplicate points must be skipped (existing logic in statistics processing).

---

## Phase 7 — Softer First-Start When Add-on May Not Be Ready

### Objective

On first setup or after a long downtime, if the add-on health or login fails, wait a short time (e.g. 15–30s) and retry once or twice before raising `ConfigEntryNotReady` or showing a hard error, so reboots don’t immediately show “add-on unreachable” when the add-on is still starting.

### Implementation

**7.1 Where to apply**

- **Setup:** In `async_setup_entry`, when we have no cookie and call `get_fresh_cookies` (and optionally `check_addon_health` first). If that fails with addon_unreachable/addon_disconnect, wait 15s and retry once (or twice). If still failing, then `ConfigEntryNotReady` or show form error.
- **Refresh path:** Optionally, when `check_addon_health` fails at the start of `_refresh_cookie_shared`, wait 15s and retry health once before giving up. Keep retry count low (1–2) so we don’t delay too long.

**7.2 Constants**

- `FIRST_START_ADDON_RETRY_DELAY_SECONDS = 15`, `FIRST_START_ADDON_RETRY_COUNT = 2`. Only use in setup path (or also first refresh after startup).

**7.3 Tests**

- Mock addon health to fail once then succeed; with delay mocked (e.g. `asyncio.sleep` patched), assert setup eventually succeeds.
- When addon always fails, assert setup raises ConfigEntryNotReady or returns False after retries.

**7.4 Risks**

- Do not delay forever; cap retries and total wait (e.g. 2 retries × 15s = 30s max).

---

## Phase 8 — Optional Learned Add-on URL

### Objective

When the user leaves addon_url as default (localhost) and we later succeed via a fallback URL (e.g. from Supervisor or from the fallback list), optionally update options with that URL (or suggest it in a one-time notification) so the next run uses it as primary.

### Implementation

**8.1 Policy**

- When we used a URL that is not the configured one (e.g. we got it from Supervisor, or we succeeded on a fallback candidate in `get_fresh_cookies`), and the configured URL was the default (localhost), optionally:
  - **Option A:** Update entry options with the working URL via `hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_ADDON_URL: working_url})`. Do this only when we’re confident (e.g. same HA instance; we could restrict to “when URL came from Supervisor” to avoid overwriting with a random fallback).
  - **Option B:** Create a one-time notification: “PSEG add-on was reached at <url>. You can save this as the Automation Add-on URL in Options for faster startup.” No automatic write.
- **Recommended:** Option B first (non-destructive). Add option `CONF_SUGGEST_LEARNED_ADDON_URL` (bool, default True). When True and we used a different URL than configured (and configured was default), create notification with the working URL once; set `hass.data[DOMAIN]["_learned_addon_url_suggested"] = True` so we don’t suggest again. Option A can be a later enhancement (e.g. “Auto-save working add-on URL” in options).

**8.2 Where to trigger**

- In `_refresh_cookie_shared`, when we get success: if `addon_url` (the one we passed) is not the same as `_get_addon_url(entry)` (the stored one), and stored one is default, and we haven’t suggested yet, create notification and set `_learned_addon_url_suggested`.

**8.3 Tests**

- When refresh succeeds with a URL different from stored default, assert notification created and `_learned_addon_url_suggested` set.
- When we’ve already suggested, do not notify again.

**8.4 Risks**

- Do not overwrite user’s custom URL. Only suggest when stored URL is the default.

---

## Test Matrix Summary

| Phase | Test file / area | Key scenarios |
|-------|-------------------|----------------|
| 1 | test_supervisor.py, test_init | Discovery returns URL or None; _get_addon_url uses it when no custom URL |
| 2 | test_auto_login_integration.py, test_init | last_working_url in candidates; stored after success |
| 3 | test_init.py | Proactive refresh by age; disabled when 0 |
| 4 | test_init.py | CAPTCHA schedules retry; success cancels; unload cancels |
| 5 | test_init.py | 3 failures → notification; success resets; cooldown |
| 6 | test_init.py (or test_statistics) | Gap > 24h → backfill; cap 30 days |
| 7 | test_init.py (setup) | Addon fail then succeed after delay; always fail → NotReady |
| 8 | test_init.py | Suggest learned URL once; no overwrite of custom URL |

---

## Files to Touch (Checklist)

- `custom_components/psegli/const.py` — New CONF_* and defaults for phases 3, 4, 5, 6, 8; ADDON_SLUG for phase 1.
- `custom_components/psegli/supervisor.py` — New (Phase 1).
- `custom_components/psegli/auto_login.py` — Phase 2: last_working_url, check_addon_health return (ok, url), get_fresh_cookies last_working_url.
- `custom_components/psegli/config_flow.py` — Options schema for new options (phases 3, 4, 5, 6, 8).
- `custom_components/psegli/__init__.py` — Phase 1: _get_addon_url + discovery; Phase 2: pass last_working_url, set _last_working_addon_url; Phase 3: proactive age check; Phase 4: CAPTCHA retry schedule; Phase 5: unreachable count and notification; Phase 6: backfill trigger in scheduler; Phase 7: setup retry delay; Phase 8: learned URL suggestion.
- `custom_components/psegli/translations/en/config_flow.json` — New option labels/descriptions.
- `tests/test_supervisor.py` — New (Phase 1).
- `tests/test_auto_login_integration.py` — Phase 2.
- `tests/test_config_flow.py` — New options defaults and validation.
- `tests/test_init.py` — Phases 2–8 as above.

---

## Document History

- 2026-03: Initial plan (all eight phases).
