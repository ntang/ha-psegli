# PSEG Long Island Home Assistant Integration

Home Assistant custom integration for PSEG Long Island usage data, with an automation add-on that handles login and cookie refresh.

## Current Architecture (Post-Robustness Overhaul)

This project now has two components:

1. `addons/psegli-automation/` (FastAPI + Playwright)
- Logs in directly to `https://mysmartenergy.psegliny.com/Dashboard`
- Handles reCAPTCHA with `playwright-stealth` + persistent browser profile
- Returns cookie string (`MM_SID` + `__RequestVerificationToken`)

2. `custom_components/psegli/` (Home Assistant integration)
- Stores and validates cookie
- Fetches usage data from PSEG endpoints
- Writes Home Assistant long-term statistics
- Schedules cookie validity checks/refresh logic

## Authentication Change (Important)

The old flow used a multi-hop login path (PSEG site -> myaccount -> Okta/MFA -> redirect).
That flow was fragile and caused repeated setup/runtime failures.

The new flow (Phase 1 of the overhaul) logs in directly on `mysmartenergy.psegliny.com`, where practical testing showed no separate Okta MFA step is required for this path.

What this means operationally:
- No `enter_mfa_code` service
- Cookie-based auth is still the runtime auth mechanism
- reCAPTCHA may appear; retries usually succeed as the persistent profile builds trust

Detailed implementation rationale and phase-by-phase history:
- [`docs/plans/2026-03-01-robustness-overhaul-plan.md`](docs/plans/2026-03-01-robustness-overhaul-plan.md)
- [`docs/plans/2026-03-02-auth-refresh-stabilization-plan.md`](docs/plans/2026-03-02-auth-refresh-stabilization-plan.md)
- [`docs/auth-overhaul.md`](docs/auth-overhaul.md)
- Known limitations and unresolved edge cases:
  [`docs/known-issues.md`](docs/known-issues.md)

## Installation

Use the detailed guide:
- [`INSTALLATION.md`](INSTALLATION.md)

Quick summary:
1. Install and start the `PSEG Long Island Automation` add-on.
2. Install the `custom_components/psegli` integration files.
3. Restart Home Assistant.
4. Add the integration in `Settings -> Devices & Services`.
5. Enter `username`, `password`, optional `cookie`.

Notes:
- Add-on is strongly recommended for stable operation.
- Manual cookie mode is possible, but you lose automated login/refresh.
- Exact manual cookie extraction + entry steps are in [`INSTALLATION.md`](INSTALLATION.md)
  under `How To Get a Manual Cookie (Exact Steps)`.
- Full cookie/login troubleshooting and script-assisted flows:
  [`docs/cookie-login-playbook.md`](docs/cookie-login-playbook.md).

## Runtime Behavior

Setup:
- If cookie is missing, integration asks add-on for fresh cookies.
- Cookie is validated before persistence.

Ongoing refresh:
- Scheduled checks run at `XX:00` and `XX:30`.
- If cookie is still valid, refresh is skipped and stats can still update.
- If cookie is expired, integration requests fresh cookie from add-on.

Phase 5 observability:
- Integration logs cookie age (runtime telemetry) at key points:
  - scheduled check still valid
  - cookie expired
  - after refresh

## Home Assistant Services

Defined in [`custom_components/psegli/services.yaml`](custom_components/psegli/services.yaml):

- `psegli.update_statistics`
  - Optional `days_back` for backfill.
- `psegli.refresh_cookie`
  - Force cookie refresh via add-on.

## Add-on API

Defined in [`addons/psegli-automation/run.py`](addons/psegli-automation/run.py):

- `GET /health`
- `POST /login` (JSON)
- `POST /login-form` (form-encoded)

See add-on docs:
- [`addons/psegli-automation/README.md`](addons/psegli-automation/README.md)

## Testing

The robustness-overhaul branch work now merged to `main` includes test coverage for:
- add-on endpoints and login flow
- integration setup/unload lifecycle
- config flow and options flow
- PSEG client error handling

Run tests locally:

```bash
python -m pytest -q
```

## Troubleshooting Quick Checks

1. Add-on not running
- Confirm add-on status in Home Assistant
- Check add-on logs
- Verify `GET /health` returns healthy

2. CAPTCHA required
- Retry setup/refresh
- Persistent profile typically reduces repeated challenges over time

3. Auth failures after running
- Call `psegli.refresh_cookie`
- Reconfigure integration and revalidate cookie

4. No new usage data
- Check Home Assistant logs for `custom_components.psegli`
- Run `psegli.update_statistics` with `days_back: 0`

## Known Issues and Edge Cases

Known unresolved limitations are documented in:
- [`docs/known-issues.md`](docs/known-issues.md)

Most auth/cookie recovery actions are documented in:
- [`docs/cookie-login-playbook.md`](docs/cookie-login-playbook.md)

## Repository

Primary repository used for these docs and merges:
- `https://github.com/ntang/ha-psegli`
