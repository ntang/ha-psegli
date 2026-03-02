# PSEG Integration Known Issues and Edge Cases

This document tracks known limitations and unresolved edge cases for the
current architecture.

Use this together with:
- Cookie/login runbook: [`docs/cookie-login-playbook.md`](cookie-login-playbook.md)
- HAOS cutover guide: [`docs/haos-migration-cutover.md`](haos-migration-cutover.md)
- Auth stabilization plan: [`docs/plans/2026-03-02-auth-refresh-stabilization-plan.md`](plans/2026-03-02-auth-refresh-stabilization-plan.md)

## Current Known Issues

### 1) CAPTCHA cannot be solved fully automatically

What happens:
- PSEG may require CAPTCHA during add-on login/refresh.
- Integration can detect and report this, but cannot solve CAPTCHA unattended.

Operator action:
- Retry `psegli.refresh_cookie`.
- If still blocked, inject manual cookie using
  [`docs/cookie-login-playbook.md`](cookie-login-playbook.md).

Status:
- By design limitation (third-party CAPTCHA challenge).

### 2) Add-on transport failures can block automatic refresh

What happens:
- HA may log `Failed to connect to addon: Server disconnected`.
- Refresh attempt fails even with valid credentials.

Operator action:
- Check add-on runtime/log health.
- Restart add-on and retry refresh.
- Use manual cookie fallback if immediate recovery is needed.

Status:
- Intermittent infrastructure/runtime edge case; mitigation exists.

### 3) Dashboard can look valid while chart context auth fails

What happens:
- Logs show `Chart setup redirected to: /` and
  `Chart setup failed — hourly context not established`.
- This indicates cookie/session is not valid for data-path chart retrieval.

Operator action:
- Trigger `psegli.refresh_cookie`.
- If refresh path fails, use manual cookie flow and re-test
  `psegli.update_statistics` (`days_back: 0`).

Status:
- Known auth edge case under active hardening in stabilization plan.

### 4) Browser-copied cookie may be unchanged or near expiry

What happens:
- Re-copying cookies can produce same values if browser session is still active.
- A manually pasted cookie can also expire soon after entry.

Operator action:
- Treat same-value cookie as potentially valid.
- Validate with `psegli.update_statistics`.
- If it fails again quickly, regenerate cookie via runbook script path.

Status:
- Expected behavior of session-cookie model.

### 5) Upstream PSEG behavior can break flows without code changes here

What happens:
- Endpoint, redirect, or anti-bot behavior changes upstream may cause new auth/data
  failures.

Operator action:
- Check HA and add-on logs.
- Attempt manual cookie recovery for service restoration.
- Capture timestamps/errors for follow-up fix planning.

Status:
- External dependency risk (cannot be fully eliminated in this repo).

### 6) Integration notifications are Home Assistant notifications

What happens:
- Integration uses HA persistent notifications.
- These are not guaranteed to become phone OS push notifications by default.

Operator action:
- Add HA automations to bridge persistent notifications to your mobile notify
  service.
- Test automation trigger variables in HA before relying on alerts.

Status:
- HA notification architecture limitation, not specific to PSEG integration code.

### 7) Routine updates currently fetch a broad recent window

What happens:
- Scheduled updates currently request a broad recent window (`days_back: 0`,
  effectively around the last 24h) on each cycle.
- This is robust for short outages but heavier than necessary for steady-state
  polling.

Operator action:
- For now, keep default behavior.
- If extended downtime occurred, run manual backfill:
  `psegli.update_statistics` with larger `days_back`.

Status:
- Planned improvement: incremental fetch based on last successful datapoint with
  bounded auto-backfill (see stabilization plan).

### 8) Extended outages may still require manual backfill today

What happens:
- Short gaps are usually recovered automatically by the broad recent fetch.
- Long gaps can exceed what is recovered automatically and may leave missing
  historical periods unless manual backfill is run.

Operator action:
- Use `psegli.update_statistics` with a suitable `days_back` to recover missed
  history.

Status:
- Planned improvement: bounded automatic catch-up/backfill in stabilization
  plan.

### 9) Latest Home Assistant test lane still has harness drift

What happens:
- Running tests against newer Home Assistant versions (2026.x) can fail due to
  test-harness/API changes (for example frame helper and options-flow behavior).
- This affects development/test reproducibility, not core runtime production
  behavior of the custom component itself.

Operator action:
- Use the stable lane (`requirements-dev.txt`, HA `2025.1.4`) for deterministic
  local validation and merge gating.
- Run the recent lane (`requirements-dev-ha-recent.txt`, HA `2026.2.0`) as a
  compatibility signal while migration work is ongoing.

Status:
- Known dev/test infrastructure limitation; forward-compatibility lane is
  available but currently informational.

## Hard Boundaries (Not Fixable in Integration Alone)

1. Solving CAPTCHA without human interaction.
2. Preventing all upstream PSEG session invalidations or anti-bot challenges.
3. Guaranteeing cookie lifetime duration.
4. Guaranteeing mobile push delivery without user-defined HA automations.

## Practical Fallback Order

1. Run `psegli.refresh_cookie`.
2. Check add-on is running and healthy.
3. Inject manual cookie via
   [`docs/cookie-login-playbook.md`](cookie-login-playbook.md).
4. Validate with `psegli.update_statistics` (`days_back: 0`).
