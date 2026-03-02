# PSEG Long Island Integration — Auth Refresh Stabilization Plan

**Date:** 2026-03-02  
**Goal:** eliminate recurring manual cookie intervention and make auth recovery automatic.

---

## Problem Statement

Live HA logs show a recurring pattern:

1. Scheduled runs at `:00`/`:30` continue executing.
2. Data path fails with:
   - `Chart setup redirected to: /`
   - `Authentication failed during update: Chart setup failed — hourly context not established`
3. Manual cookie injection restores operation immediately.
4. Add-on refresh sometimes fails with:
   - `Failed to connect to addon: Server disconnected`

This indicates a gap between lightweight cookie checks and actual chart-context auth, plus add-on refresh reliability issues.

---

## Desired End State

1. Integration detects data-path auth failure automatically.
2. Integration forces refresh on data-path auth failure without waiting for manual action.
3. Add-on refresh path handles transient disconnects with bounded retries.
4. Persistent notifications clearly distinguish:
   - add-on connectivity failure
   - CAPTCHA challenge
   - chart-context auth failure loop
5. Operators can run with high observability initially, then reduce noise later via config.
6. Manual cookie entry remains fallback, not primary recovery path.
7. Routine data sync avoids unnecessary broad fetch windows while preserving automatic catch-up.

---

## Delivery Sequencing

Preferred shipping sequence:
1. Ship Phases 1-3 first (auth correctness + refresh hardening + observability).
2. Ship Phase 4 next (incremental fetch + bounded auto-backfill) once new
   signals are available.
3. Apply Phases 5-7 tests/rollout/docs for each release boundary.

---

## Phase 1 — Close the Auth Validation Gap

### 1.1 Add a data-path auth probe in `PSEGLIClient`

Implement a synchronous method (executor-run by integration) that validates the same auth path used by statistics updates:
- dashboard fetch
- token extraction
- chart context setup

Implementation notes:
- Reuse existing internals to avoid drift:
  - `_get_dashboard_page()`
  - `_setup_chart_context()`
- Do **not** fetch chart data in the probe (`_get_chart_data()` is intentionally skipped).
- Probe must be read-only for integration state:
  - no statistics writes
  - no config updates
  - no cookie persistence side effects

Error mapping requirements for probe path:
- 5xx or transport failures -> `PSEGLIError` (transient/retryable)
- 4xx/login redirect/chart redirect auth failures -> `InvalidAuth`

Implementation requirement:
- Probe wrapper must catch transport/HTTP exceptions raised from
  `_setup_chart_context()` and map them to `PSEGLIError` so 5xx is never
  misclassified as auth failure.

Do not rely only on `/Dashboard` success as auth-valid signal.

### 1.2 Use data-path probe where scheduler currently uses lightweight check

Replace “cookie still valid” determination in scheduled flow with the data-path probe result.
- If probe fails with auth redirect/error, treat cookie as expired/invalid.
- Proceed directly to refresh attempt.
- If probe passes, still run normal `_do_update_statistics()` once (no probe chart data call).

---

## Phase 2 — Automatic Recovery on Update Failure

### 2.1 Promote chart-context auth failure to refresh trigger

When `_do_update_statistics` hits auth errors such as:
- `Chart setup redirected to: /`
- chart setup auth failure

mark state as “refresh required” and trigger refresh logic with a short
coalescing delay (`10s`) instead of waiting for next cycle only.

Re-entrancy requirements:
- Add refresh lock/flag (`_refresh_in_progress`) to prevent parallel refresh attempts from:
  - scheduled loop
  - manual `psegli.refresh_cookie`
  - immediate-recovery trigger from failed update
- Use one shared refresh helper for all three call sites to keep behavior/logging aligned.
- If refresh is already in progress, additional callers wait for the in-flight
  refresh and receive the same final result (single-flight behavior).

### 2.2 Prevent infinite fail loops

Track consecutive auth-failure count in `hass.data[DOMAIN]`.
Failure loop policy:
- Threshold: `N = 3` consecutive auth failures.
- After N failures, emit a dedicated persistent notification:
  - `psegli_chart_auth_failed_loop`
- Counter reset conditions:
  - successful cookie refresh, or
  - successful statistics update.
- Retry cadence:
  - continue attempts each scheduled cycle (simple, predictable),
  - but apply notification cooldown/suppression to avoid alert storms.
- Cooldown window:
  - `24h` for repeated `psegli_chart_auth_failed_loop` notifications.
- Manual intervention behavior:
  - manual cookie update via options flow resets `consecutive_auth_failures`
    immediately (does not wait for next successful stats run).

---

## Phase 3 — Harden Add-on Refresh Reliability

### 3.1 Retry policy for add-on `/login` disconnects

On add-on connectivity failures (`Server disconnected`, timeout):
- retry 2-3 times with short jittered backoff.
- keep upper-bound latency reasonable.

Scope:
- Retries apply **only** to transport/connectivity failures:
  - connection error
  - timeout
  - server disconnected
- Do **not** retry terminal functional responses:
  - `captcha_required`
  - invalid credentials / explicit login failure response

Location:
- Implement retry policy in integration add-on client path
  (`custom_components/psegli/auto_login.py`), not inside the add-on service.

### 3.2 Improved diagnostics

Log refresh attempt IDs and classify failure reasons:
- addon_unreachable
- addon_disconnect
- captcha_required
- invalid_credentials
- unknown_runtime_error

This avoids “generic refresh failed” ambiguity.
These categories should drive both logs and notification text.

### 3.3 Configurable observability and HA-facing signals

Add explicit runtime controls (Options flow) so operators can tune verbosity:
- `diagnostic_level`: `standard` (default) or `verbose`
- `notification_level`: `critical_only` (default) or `verbose`

Backward compatibility:
- Existing config entries without these options default to:
  - `diagnostic_level=standard`
  - `notification_level=critical_only`

Signal model (stored in `hass.data[DOMAIN]` and exposed via diagnostics + service):
- `last_auth_probe_at`
- `last_auth_probe_result` (`ok`, `invalid_auth`, `transient_error`)
- `last_refresh_attempt_at`
- `last_refresh_reason` (`scheduled`, `update_auth_failure`, `manual_service`)
- `last_refresh_result` (`success`, `failed`)
- `last_refresh_failure_category` (from 3.2 categories)
- `consecutive_auth_failures`
- `last_successful_update_at`
- `last_successful_datapoint_at` (max timestamp ingested)
- `cookie_age_seconds` (when known)

Signal access:
- Include these fields in config-entry diagnostics output.
- Add a lightweight `psegli.get_status` service returning current signal snapshot
  for dashboards/automations/debugging.

Logging policy:
- `standard`: one-line state transitions and actionable failures only.
- `verbose`: include probe/refresh decision breadcrumbs, retry decisions, and cooldown suppression events.

Notification policy:
- `critical_only`: CAPTCHA required, repeated auth-failure loop, sustained add-on unreachable condition.
- `verbose`: include transient refresh-retry failures and recoveries.

---

## Phase 4 — Incremental Data Window and Automatic Backfill

### 4.1 Reduce routine fetch window using last successful datapoint

Current behavior fetches roughly the last 24h on every scheduled run.

Implement incremental fetch planning:
- Track `last_successful_datapoint_at` (UTC) after successful ingestion.
- Persist/restore behavior:
  - on startup, if in-memory `last_successful_datapoint_at` is missing,
    derive it from latest recorder statistics timestamp when possible;
    if unavailable, fall back to one broad recent-window fetch for that run.
- Compute routine fetch start from:
  - `last_successful_datapoint_at - overlap`, where overlap defaults to `1h`.
- Because upstream chart API is date-granular (`Start`/`End` date strings),
  normalize requested `Start` to that timestamp's date.
- Keep `End` at current date.
- Filter/ignore points already ingested (`point.timestamp <= last_successful_datapoint_at`)
  before writing statistics.

### 4.2 Add bounded automatic backfill for larger gaps

If detected gap between now and `last_successful_datapoint_at` exceeds
`24h` (`auto_backfill_trigger_hours` default), then:
- automatically widen fetch window to cover missing period.
- cap automatic backfill with `max_auto_backfill_days` (default `30`).
- if required gap exceeds cap:
  - fetch up to cap,
  - emit explicit notification instructing operator to run manual backfill
    (`psegli.update_statistics` with larger `days_back`).

Backfill reset conditions:
- On successful catch-up, collapse back to routine incremental window.
- Record last successful backfill range for diagnostics.

---

## Phase 5 — Tests and Regression Coverage

Add tests that reproduce real failure sequence:

1. Data-path probe sees chart setup redirect (`/`) -> scheduler must refresh (no stale-valid decision).
2. Add-on disconnect on first refresh attempt, success on second -> automatic recovery.
3. Consecutive chart auth failures -> loop notification emitted.
4. Re-entrancy guard prevents concurrent refresh attempts across scheduler/manual/immediate-recovery paths.
5. Retry policy only retries transport failures and not CAPTCHA/invalid-credentials responses.
6. Observability controls:
   - `standard` vs `verbose` logging behavior
   - `critical_only` vs `verbose` notification behavior
   - signal fields update correctly on success/failure transitions
7. In-flight refresh single-flight behavior:
   - second caller waits for existing refresh and receives same outcome.
8. Incremental data window behavior:
   - fetch planning uses `last_successful_datapoint_at - 1h`.
   - duplicate/old points are skipped.
   - startup restore fallback behavior works when in-memory state is empty.
9. Automatic backfill behavior:
   - gap > `auto_backfill_trigger_hours` (default `24h`) triggers bounded backfill.
   - gap > `max_auto_backfill_days` triggers operator notification.

Also keep existing lifecycle/startup guarantees:
- scheduler runs as background task
- no startup blocking warnings.

---

## Phase 6 — Rollout and Validation

### 6.1 Staged rollout checks

After deployment:
1. Force `psegli.update_statistics` and verify success.
2. Force `psegli.refresh_cookie` and verify add-on path.
3. Observe at least two scheduled checkpoints (`:00`/`:30`).

### 6.2 Acceptance criteria

All must be true:
1. No sustained `Chart setup redirected to: /` loop over 24h.
2. At least one simulated add-on disconnect is auto-recovered without manual cookie.
3. No startup timeout/blocking warnings from PSEG scheduled task.
4. Manual cookie remains optional fallback only.
5. Simulated N=3 consecutive chart auth failures emits `psegli_chart_auth_failed_loop` exactly once per `24h` cooldown window.
6. Operators can switch from `verbose` to `standard` and observe reduced log/notification volume without losing critical alerts.
7. Routine scheduled updates avoid full 24h refetch in steady state (verified by logs/status signals).
8. After simulated outage >24h, integration auto-catches up within configured backfill cap without manual intervention.

---

## Phase 7 — Documentation and Operator Guidance Updates

Any behavior/system change in this plan must ship with matching documentation
updates in the same PR/commit series.

Required documentation updates:
1. Update [`README.md`](../../README.md):
   - explain incremental fetch behavior
   - explain automatic backfill bounds and manual backfill fallback
   - list any new service(s) and signal visibility behavior
2. Update [`INSTALLATION.md`](../../INSTALLATION.md):
   - add validation steps for new behavior after upgrade
   - add operator steps for bounded auto-backfill and manual overflow handling
3. Update [`docs/cookie-login-playbook.md`](../cookie-login-playbook.md):
   - clarify auth-failure handling flow with new refresh/signal behavior
4. Update [`docs/known-issues.md`](../known-issues.md):
   - move resolved items out of "known issues"
   - keep only real residual limitations/edge cases
5. Add changelog/release notes:
   - include user-visible behavior changes and migration notes
   - include any new defaults/options and alerting behavior

Documentation acceptance criteria:
1. No code change in this plan merges without corresponding doc updates.
2. Operator runbooks include explicit verification steps for new behavior.
3. Known issues doc reflects current state after rollout (resolved vs remaining).

---

## Out of Scope

1. Replacing cookie auth model entirely.
2. Reworking PSEG upstream endpoint behavior.
3. Full mobile notification UX redesign.

---

## Remaining Edge Cases (After This Plan)

1. PSEG invalidates sessions in ways that still return partially successful HTML before failing deeper in data fetch.
2. CAPTCHA required but operator is unavailable; automation can alert but cannot solve CAPTCHA.
3. Extended upstream outage where retries continue but data remains unavailable for prolonged periods.
4. Browser-copied cookie may be near-expiry when pasted manually, causing short-lived recovery.
5. Add-on process healthy but Playwright/browser runtime degraded in ways that surface as unknown runtime failures (`unknown_runtime_error`).

---

## Release Note

Implementation of this plan should be shipped as a patch release with changelog entries
covering:
1. data-path probe adoption
2. immediate refresh-on-auth-failure
3. add-on transport retry hardening
4. new diagnostics/notifications/signals
5. incremental fetch window + bounded automatic backfill
6. documentation/runbook updates aligned with final behavior
