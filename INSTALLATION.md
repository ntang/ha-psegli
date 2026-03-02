# PSEG Long Island Integration Installation Guide

This guide covers the post-overhaul install flow and auth model.

## Prerequisites

- Home Assistant OS, Supervised, or Core
- PSEG Long Island account credentials (email/username + password)
- Access to your Home Assistant config directory

## Install Order (Recommended)

1. Install and start the automation add-on
2. Install the custom integration
3. Configure integration with credentials

This order gives the integration immediate access to automated cookie retrieval.

## 1) Install the Automation Add-on

Add-on source repository:
- `https://github.com/ntang/ha-psegli`

In Home Assistant:
1. Go to `Settings -> Apps -> Install app`
2. Open menu `... -> Repositories`
3. Add `https://github.com/ntang/ha-psegli`
4. Install `PSEG Long Island Automation`
5. Start the add-on

Verification:
- Add-on status is `Running`
- Add-on logs do not show startup errors

Technical endpoint check (optional):
- `GET /health` should return healthy from the add-on service

## 2) Install the Integration

### Option A: HACS (if using HACS)

1. In HACS, add this repo as a custom integration repository:
- `https://github.com/ntang/ha-psegli`
2. Install `PSEG Long Island`
3. Restart Home Assistant

### Option B: Manual install

1. Copy integration folder into HA config:

```bash
cp -r custom_components/psegli /config/custom_components/
```

2. Restart Home Assistant

## Update Existing Installation

Use this when upgrading an already-installed setup.

1. Update integration code (`custom_components/psegli`):
- HACS: update the integration in HACS, then restart Home Assistant.
- Manual: replace `/config/custom_components/psegli` with the latest folder from this repo, then restart Home Assistant.
2. Update/rebuild add-on (`PSEG Long Island Automation`):
- Go to `Settings -> Apps -> Install app -> PSEG Long Island Automation`.
- Click `Update` if available. If `Update` is not shown, click `Rebuild`.
- Start the add-on and confirm state is `Running`.
3. Restart Home Assistant after both integration + add-on are updated.

Validation after update:
- `Settings -> Devices & Services -> PSEG Long Island` loads (not failed)
- `Developer Tools -> Actions` shows:
  - `psegli.refresh_cookie`
  - `psegli.update_statistics`
- Running `psegli.update_statistics` with `days_back: 0` succeeds

## 3) Configure the Integration

In Home Assistant:
1. Go to `Settings -> Devices & Services`
2. Click `Add Integration`
3. Search for `PSEG Long Island`
4. Enter:
- `username`
- `password`
- optional `cookie`

Behavior during setup:
- If `cookie` is empty, integration tries add-on login first.
- If add-on returns cookie, integration validates it before saving.
- If CAPTCHA is triggered, setup shows a `captcha_required` error; retry.

## Auth Model Changes (from legacy flow)

Old model:
- Multi-step path through myaccount/Okta/MFA

Current model:
- Direct login to `mysmartenergy.psegliny.com` via add-on browser automation
- Cookie string is the runtime auth token used by the integration
- No MFA submission service (`enter_mfa_code`) in current architecture

Why this changed:
- The old chain was operationally fragile and caused repeated auth/setup failures.
- The direct flow is simpler and has better reliability in production.

## Manual Cookie Mode (Fallback)

The integration can run with a manually provided cookie if the add-on is unavailable.

Caveats:
- No automated browser login
- No automatic recovery when cookie expires
- You must reconfigure/update cookie manually

### How To Get a Manual Cookie (Exact Steps)

1. Open `https://mysmartenergy.psegliny.com/Dashboard` in a normal desktop browser.
2. Sign in fully with your PSEG credentials.
3. Open browser developer tools:
- Chrome/Edge: `Cmd+Option+I` (Mac) or `F12` (Windows/Linux)
- Firefox: `Cmd+Option+I` (Mac) or `F12`
- Safari: enable Developer menu, then `Develop -> Show Web Inspector`
4. Open cookie storage for `https://mysmartenergy.psegliny.com`:
- Chrome/Edge: `Application -> Storage -> Cookies -> https://mysmartenergy.psegliny.com`
  (if `Application` tab is hidden, click `>>`)
- Firefox: `Storage -> Cookies -> https://mysmartenergy.psegliny.com`
- Safari: `Storage -> Cookies -> mysmartenergy.psegliny.com`
5. Copy values for both cookies:
- `MM_SID`
- `__RequestVerificationToken`
6. Build one cookie string exactly:

```text
MM_SID=<MM_SID_VALUE>; __RequestVerificationToken=<TOKEN_VALUE>
```

Rules:
- Include both cookie names exactly as written.
- Keep the semicolon separator: `; `
- Do not add quotes.

### Where To Enter the Manual Cookie in Home Assistant

New install:
1. `Settings -> Devices & Services -> Add Integration -> PSEG Long Island`
2. Enter `username` and `password`
3. Paste cookie string into `cookie`
4. Submit

Existing install:
1. `Settings -> Devices & Services -> Integrations -> PSEG Long Island`
2. Open card/details menu and choose `Configure` or `Options` (label varies by HA version)
3. Paste cookie string into `cookie`
4. Submit

If `Configure`/`Options` is missing:
- Delete the failed PSEG integration entry
- Restart Home Assistant
- Re-add integration and provide credentials/cookie in initial add flow

Script-assisted alternative:
- See [`docs/cookie-login-playbook.md`](docs/cookie-login-playbook.md)
  for local Playwright script commands that generate/test a cookie string.

## Runtime Refresh Behavior

- Scheduled checks occur at `XX:00` and `XX:30`.
- If cookie validates, refresh is skipped.
- If invalid, integration requests fresh cookie from add-on.
- Manual refresh service is available: `psegli.refresh_cookie`.

## Verify Successful Operation

1. Integration loads without auth errors
2. `psegli.update_statistics` service succeeds
3. Long-term statistics appear in Energy Dashboard
4. Logs show successful data fetch and no repeated auth failures

## Troubleshooting

### Add-on unavailable

- Confirm add-on is running
- Check add-on logs
- Restart add-on and retry integration setup

### CAPTCHA required repeatedly

- Retry setup/refresh later
- Persistent browser profile usually reduces repeated CAPTCHA prompts

### Invalid auth

- Reconfigure integration and/or call `psegli.refresh_cookie`
- Verify credentials are current and account is not locked

### No data updates

- Run `psegli.update_statistics` with `days_back: 0`
- Check logs under `custom_components.psegli`

## Related Docs

- Root overview: [`README.md`](README.md)
- Add-on details: [`addons/psegli-automation/README.md`](addons/psegli-automation/README.md)
- Integration details: [`custom_components/psegli/README.md`](custom_components/psegli/README.md)
- Cookie/login details: [`docs/cookie-login-playbook.md`](docs/cookie-login-playbook.md)
- Overhaul plan: [`docs/plans/2026-03-01-robustness-overhaul-plan.md`](docs/plans/2026-03-01-robustness-overhaul-plan.md)
- Auth migration notes: [`docs/auth-overhaul.md`](docs/auth-overhaul.md)
