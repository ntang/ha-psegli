# Authentication Overhaul Notes

## Purpose

This document explains what changed in the authentication system, why it changed, and what operators should expect now.

Primary implementation plan and review trail:
- [`docs/plans/2026-03-01-robustness-overhaul-plan.md`](plans/2026-03-01-robustness-overhaul-plan.md)

## Before vs After

### Before (legacy)

- Browser automation followed a multi-hop chain:
  - PSEG homepage
  - myaccount login
  - Okta/MFA interactions
  - redirect to mysmartenergy
- Runtime frequently depended on MFA-related paths and fragile page assumptions.
- Several failure modes could disable integration instead of retrying cleanly.

### After (current)

- Add-on logs in directly to:
  - `https://mysmartenergy.psegliny.com/Dashboard`
- Runtime auth token remains cookie-based:
  - `MM_SID`
  - `__RequestVerificationToken`
- Integration validates cookie before persisting it.
- Scheduler checks validity before refreshing, then refreshes only when needed.
- Error handling separates transient network failures from true auth failures.

## Why This Change Was Made

From live testing and review findings:

1. Direct mysmartenergy login was enough for this flow.
2. The old Okta/MFA chain added complexity and instability.
3. reCAPTCHA could be handled more reliably with:
- `playwright-stealth`
- persistent browser profile
4. Integration lifecycle and config flow needed stronger tests to prevent regressions.

## Operational Expectations

- No `enter_mfa_code` service in the new architecture.
- If CAPTCHA is triggered, retry usually succeeds after profile trust builds.
- Scheduled refresh checks run at `XX:00` and `XX:30`.
- Manual service `psegli.refresh_cookie` can force recovery.

## Scope of the Robustness Overhaul

Phases merged to `main`:

- Phase 1: Direct login architecture rewrite
- Phase 2: Client synchronization/timeouts/error-separation cleanup
- Phase 3: Statistics and scheduler robustness fixes
- Phase 4.0-4.12: Test expansion and review-driven bug fixes
- Phase 5: Cookie lifetime observability logging

## Remaining Low-Priority Items

As of the plan completion, remaining items are hygiene-level (Very Low priority), not core auth correctness blockers.

## Related Files

- Add-on login flow: [`../addons/psegli-automation/auto_login.py`](../addons/psegli-automation/auto_login.py)
- Add-on API: [`../addons/psegli-automation/run.py`](../addons/psegli-automation/run.py)
- Integration setup/scheduler: [`../custom_components/psegli/__init__.py`](../custom_components/psegli/__init__.py)
- Config/options flow: [`../custom_components/psegli/config_flow.py`](../custom_components/psegli/config_flow.py)
