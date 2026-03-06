# PSEG Long Island Integration — Add-on Automation & Robustness Plan

**Date:** 2026-03-06  
**Goal:** Reduce manual intervention to near-zero by making add-on connectivity, auth recovery, and data continuity self-healing.

---

## Current Baseline (Already Implemented)

- Configurable add-on URL in config/options flow (`addon_url`), including translations.
- Retry with jitter for add-on `/login` transport failures.
- Multi-candidate URL probing on transport errors (`localhost` + known fallback DNS patterns).
- Auto-promotion of discovered working add-on URL into options after successful fallback.
- Richer add-on login diagnostics (failure context + artifacts) and integration observability signals (`get_status`).

This plan focuses on what remains.

---

## Feedback Synthesis (Claude + Cursor)

| Idea | Decision | Why |
|---|---|---|
| 1) Supervisor add-on URL discovery | **Adopt now** | Highest leverage for auto-config; removes URL guesswork on HAOS/Supervised. |
| 2) Browser profile health check on add-on startup | **Adopt now** | Catches profile corruption early and avoids opaque runtime failures. |
| 3) CAPTCHA auto-retry with backoff | **Adopt now** | reCAPTCHA is often transient; retries should reduce manual refresh calls. |
| 4) Add-on `/profile-status` endpoint | **Adopt now** | Enables proactive decisions from integration (warm-up, stale profile risk, last success). |
| 5) Incremental fetch + bounded auto-backfill | **Adopt now** | Improves outage recovery and reduces unnecessary load. |
| 6) Profile warm-up after reset | **Adopt now** | Front-loads trust-building and reduces first-user-action failures. |
| 7) Circuit breaker for add-on connectivity | **Adopt now** | Prevents retry storms and log spam during outages. |
| 8) Proactive cookie expiry notification | **Adopt now** | User gets warning before failure mode, not after. |
| 9) One-click setup/install wizard | **Modify** | Full install/start control is Supervisor-permission dependent; implement guided readiness checks first. |
| 10) Profile backup/restore | **Defer** | Sensitive state and lifecycle complexity; revisit after profile-status + warm-up telemetry prove need. |

---

## Revised Delivery Order

Recommended order:

1. **Phase A:** Supervisor discovery + URL canonicalization
2. **Phase B:** Circuit breaker + unreachable notification hardening
3. **Phase C:** CAPTCHA auto-retry and first-start grace retry
4. **Phase D:** Add-on profile health + warm-up + `/profile-status`
5. **Phase E:** Proactive refresh + proactive expiry notification
6. **Phase F:** Incremental fetch + bounded backfill
7. **Phase G:** Guided setup/readiness UX (not full add-on install automation)
8. **Phase H:** Optional deferred profile backup/restore RFC

Rationale: establish deterministic connectivity first, then recovery behavior, then data completeness, then UX polish.

---

## Initial Implementation Defaults (v1)

These are execution defaults so implementation work can proceed without re-litigating constants:

- **Supervisor discovery cache TTL:** `60s` in `hass.data[DOMAIN]`.
- **Circuit breaker open threshold (N):** `3` consecutive transport failures.
- **Circuit breaker open duration (M):** `10 minutes`.
- **Half-open probes:** `1` probe; close on success, re-open on failure.
- **CAPTCHA auto-retry count:** `2` retries.
- **CAPTCHA auto-retry delays:** `[5, 15]` minutes.
- **First-start grace retries:** `2` retries with `15s` delay.
- **Proactive refresh max age:** `20h` (0 disables).
- **Expiry warning threshold:** `80%` of observed/assumed cookie lifetime.
- **Auto-backfill trigger:** `24h` datapoint gap.
- **Max auto-backfill window:** `30 days`.

These values should be constants/options with tests and can be tuned after telemetry.

---

## Phase A — Supervisor Discovery + URL Canonicalization

### Objective

Automatically resolve the add-on URL from Supervisor when available and normalize all URL sources to one canonical effective URL.

### Scope

- Add `custom_components/psegli/supervisor.py`:
  - `async_get_addon_url_from_supervisor(hass) -> str | None`
  - short timeout, safe failure handling, no hard dependency on Supervisor.
- In `__init__.py`, `_get_addon_url(...)` behavior:
  - precedence: explicit options URL > explicit data URL > Supervisor discovered URL > default.
- Keep current learned URL promotion behavior.
- Cache discovered Supervisor URL for a short TTL in `hass.data[DOMAIN]`.
  - **Important scope:** cache is only to avoid repeated Supervisor API calls in-cycle; persistent URL learning remains owned by existing runtime auto-promotion.

### Tests

- new `tests/test_supervisor.py`: success / 404 / timeout / malformed payload.
- `tests/test_init.py`: precedence and fallback ordering with/without explicit URL.

### Risks

- Supervisor absent in non-Supervised installs: must degrade gracefully.

---

## Phase B — Circuit Breaker + Unreachable Notifications

### Objective

Avoid hammering add-on endpoints during outages while still probing for recovery.

### Scope

- Add transport-failure circuit breaker in integration domain state:
  - closed -> open after N consecutive transport failures.
  - open duration M minutes, then half-open single probe.
  - close on success.
- Extend unreachable notifications:
  - threshold-based and cooldown-based persistent notifications.
  - include active effective URL, last working URL, and next probe time.

### Tests

- Failure count opens breaker.
- Open state suppresses repeated probes.
- Half-open probe success closes breaker.
- Notification cooldown enforcement.

---

## Phase C — CAPTCHA Auto-Retry + First-Start Grace

### Objective

Treat CAPTCHA as a recoverable state with controlled delayed retries.

### Scope

- Add options:
  - `captcha_auto_retry_count`
  - `captcha_auto_retry_delays_minutes`
- On CAPTCHA result:
  - schedule delayed retries (bounded).
  - cancel pending retries on success/unload.
- First-start grace behavior:
  - on startup/add-on race conditions, short retry window before hard failure path.

### Tests

- CAPTCHA schedules retries.
- Retry success cancels remaining retries.
- Unload cancels timers.
- First-start transient failure then success.

---

## Phase D — Add-on Profile Health, Warm-Up, and `/profile-status`

### Objective

Make browser-profile issues observable and self-healing.

### Add-on Changes

- Startup profile sanity check:
  - launch context quick probe.
  - on corruption, rotate profile directory and continue.
- Add `/profile-status` endpoint with:
  - profile_created_at
  - profile_last_success_at
  - recent_captcha_count
  - profile_size_bytes
  - warmup_state (`idle|warming|ready|failed`)
- Optional warm-up flow after fresh/rotated profile:
  - perform safe trust-building login attempt sequence.

### Integration Changes

- Query `/profile-status` during health flow (best effort).
- Adjust logs/notifications when `warmup_state != ready`.

### Tests

- Endpoint payload contract.
- Rotated profile path on simulated corruption.
- Warm-up state transitions.

---

## Phase E — Proactive Refresh + Proactive Expiry Warning

### Objective

Refresh before expected expiry and warn user pre-failure.

### Scope

- Options:
  - `proactive_refresh_max_age_hours` (0 disables)
  - `expiry_warning_threshold_percent` (e.g., 80%)
- Scheduler:
  - if cookie age exceeds proactive threshold, refresh before probe/update.
- Notifications:
  - one warning per cooldown window when nearing expiry and refresh path unhealthy.

### Tests

- Age threshold triggers proactive refresh.
- Disabled setting bypasses proactive refresh.
- Warning notifications respect cooldown.

---

## Phase F — Incremental Fetch + Bounded Backfill

### Objective

Recover missed history automatically while minimizing routine API volume.

### Scope

- Use `last_successful_datapoint_at` as primary cursor.
- Routine updates fetch only required window.
- On gap > trigger threshold, run bounded backfill:
  - `days_back = min(calculated_days, max_auto_backfill_days)`.
- Keep duplicate-safe statistics writes.

### Tests

- Short gap -> incremental only.
- Large gap -> capped backfill.
- Post-backfill cursor update.

---

## Phase G — Guided Setup/Readiness UX

### Objective

Eliminate user confusion without requiring privileged add-on install actions.

### Scope

- Config flow preflight step:
  - detect Supervisor availability.
  - detect add-on installed/running/reachable.
  - show actionable next step with exact remediation.
- Avoid hard-failing when add-on not yet ready; allow setup continuation with clear status.

### Note

- Full “one-click install/start add-on” is intentionally not assumed; implement only if Supervisor permissions are available and secure.

### Tests

- Preflight states: ready / not installed / installed-not-running / unreachable.

---

## Phase H — Deferred RFC: Profile Backup/Restore

### Decision

Defer implementation. Draft RFC only after telemetry from Phases D–F.

### Why Deferred

- Contains sensitive auth-adjacent browser state.
- Risk of restoring stale/corrupted state.
- Adds lifecycle and migration complexity.

---

## Cross-Cutting Requirements

- **No destructive overwrites of user custom URL.**
- **All fallback/learning decisions are observable in logs + `get_status`.**
- **Retry paths remain bounded.**
- **Every phase ships with tests and explicit rollback behavior.**
- **Stable lane (`requirements-dev.txt`) remains release gate.**

---

## Parallel Execution Map

Phases that can run in parallel after prerequisites:

- **Wave 1 (sequential prerequisite):** `Phase A` should land first to stabilize URL resolution contract.
- **Wave 2 (parallel after A):**
  - `Phase B` (breaker/notifications)
  - `Phase C` (CAPTCHA auto-retry + first-start grace)
  - `Phase D` (add-on profile-status + warm-up)
- **Wave 3 (parallel after C + D):**
  - `Phase E` (proactive refresh/expiry warning)
  - `Phase F` (incremental fetch/backfill)
- **Wave 4 (after A–F):**
  - `Phase G` (guided setup UX)
- **Wave 5 (optional):**
  - `Phase H` RFC only

Parallelization constraints:

- `Phase D` defines `/profile-status` contract consumed by `Phase E/G`.
- `Phase B` breaker state must be compatible with `Phase C/E` schedulers.
- `Phase F` should reuse existing signal semantics (`last_successful_datapoint_at`) and not fork data-path logic.

---

## Implementation Notes for Executors

To reduce ambiguity when coding, use these concrete contracts:

- `custom_components/psegli/supervisor.py`
  - `async_get_addon_url_from_supervisor(hass) -> str | None`
- `custom_components/psegli/auto_login.py`
  - **`LoginResult.addon_url`** is already implemented: non-transport responses (success, CAPTCHA, invalid_credentials, 4xx) carry the endpoint URL that returned the response; full disconnect (exhausted retries) leaves it `None`. Phase B (breaker/notifications) should rely on this field for “last working URL” and must not remove or change this semantics.
  - candidate ordering remains deterministic and deduped.
- `custom_components/psegli/__init__.py`
  - keep `_get_addon_url` precedence: `options > data > supervisor > default`.
  - only persist discovered URL when it differs and user has not set a conflicting custom URL.
- `hass.data[DOMAIN]` keys added in this plan should be prefixed consistently and cleaned/reset on unload where needed.

---

## File Impact Map

- `custom_components/psegli/const.py`
- `custom_components/psegli/supervisor.py` (new)
- `custom_components/psegli/auto_login.py`
- `custom_components/psegli/__init__.py`
- `custom_components/psegli/config_flow.py`
- `custom_components/psegli/translations/en/config_flow.json`
- `addons/psegli-automation/auto_login.py`
- `addons/psegli-automation/profile_state.py` (new, Phase D)
- `addons/psegli-automation/run.py`
- `tests/test_supervisor.py` (new)
- `tests/test_auto_login_integration.py`
- `tests/test_init.py`
- `tests/test_config_flow.py`
- `tests/test_addon_login.py`
- `tests/test_addon_endpoints.py`

---

## Exit Criteria

- URL auto-discovery works on HAOS/Supervised without manual URL entry.
- Outages no longer cause retry storms.
- CAPTCHA/manual-retry incidents are materially reduced.
- Profile corruption/reset paths are detectable and recoverable.
- Missed data is automatically backfilled within bounded limits.
- All new behaviors covered by deterministic tests.

---

## Changelog for This Plan Revision

- Integrated Claude’s recommendations into Cursor’s phase model.
- Updated scope to reflect already-landed URL fallback + auto-promotion work.
- Added decision table (adopt/modify/defer) and explicit deferred RFC boundary.
- **d967dd7:** Added initial implementation defaults (v1), Phase A cache scope clarification (in-cycle vs persistent learning), parallel execution waves and constraints, and executor-facing implementation contracts.
- **Phases D + G implemented (Cursor):** Add-on profile health + rotate on corruption, `/profile-status` endpoint and warm-up, integration profile-status fetch and warmup_state logging; config flow preflight (add-on readiness + remediation message). Tests: profile-status contract, rotation on launch failure, get_addon_profile_status, preflight ready/unreachable.
