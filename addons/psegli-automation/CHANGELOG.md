# Changelog

## Unreleased

- Add retry with jittered backoff for add-on `/login` transport failures (connection error, timeout, server disconnected)
  - Up to 3 attempts with increasing delay
  - Terminal responses (CAPTCHA, invalid credentials) are never retried
  - Implements Phase 3.1 of auth refresh stabilization plan
- Classify refresh failure reasons into diagnostic categories: `addon_disconnect`, `captcha_required`, `invalid_credentials`, `unknown_runtime_error`
- Return structured `LoginResult` from `get_fresh_cookies` with `.cookies` and `.category` fields
- Add refresh attempt IDs (`[refresh:XXXXXXXX]`) to all log messages for correlation
- Track auth probe, refresh, update, and datapoint signals in `hass.data` for observability
- Add `psegli.get_status` service returning integration signal snapshot (auth probes, refresh state, cookie age, etc.)
- Add `diagnostic_level` (standard/verbose) and `notification_level` (critical_only/verbose) options
  - Standard: one-line state transitions and actionable failures only
  - Verbose: include probe/refresh decision breadcrumbs and transient retry notifications
- Implements Phase 3.2 (improved diagnostics) and Phase 3.3 (configurable observability)

## 2.5.0.5

- Use background task API for scheduled cookie refresh to avoid HA startup blocking warnings
- Add Home Assistant statistics metadata compatibility updates for `mean_type` and `unit_class`
- Centralize release version management with a single `VERSION` source and sync tooling
- Improve docs for installation, cookie retrieval, and script-assisted manual cookie workflows

## 2.5.0.4

- Harden dashboard token extraction for chart context:
  - DOM-based extraction (attribute-order agnostic)
  - Fallback to cookie token when hidden input is absent

## 2.5.0.3

- Version and metadata alignment updates

## 2.5.0.2

- Retry setup automatically when add-on cookie retrieval fails (`ConfigEntryNotReady`)

## 2.5.0.1

- Fix options flow compatibility with newer Home Assistant API behavior
