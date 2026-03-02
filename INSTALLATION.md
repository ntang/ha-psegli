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
- Overhaul plan: [`docs/plans/2026-03-01-robustness-overhaul-plan.md`](docs/plans/2026-03-01-robustness-overhaul-plan.md)
- Auth migration notes: [`docs/auth-overhaul.md`](docs/auth-overhaul.md)
