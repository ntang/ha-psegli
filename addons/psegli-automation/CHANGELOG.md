# Changelog

## Unreleased

## 2.5.2

- Complete consolidated robustness hardening across integration and add-on:
  - Close add-on/integration taxonomy gaps with deterministic category mapping
  - Add `transient_site_error` handling for upstream 5xx/transient failures
  - Keep category-aware refresh notifications and retry/remediation guidance aligned
- Add persistent login-failure artifact capture and retrieval:
  - Store redacted HTML/screenshot artifacts under `/data/login_failures`
  - Retain latest 10 artifacts with pruning on startup and after new writes
  - Expose metadata-only `GET /artifacts/login-failures`
- Extend observability and diagnostics:
  - Unify `get_status` and diagnostics through one async snapshot builder
  - Add artifact summary fields: `artifact_count`, `artifact_latest_created_at`, `artifact_list_endpoint`
  - Keep all existing signal fields intact
- Finalize CAPTCHA governance:
  - Preserve retry origin context (for example `captcha_auto_retry_1:manual_service`)
  - Suppress repeated CAPTCHA notifications in standard mode
  - Keep retry-attempt notifications in verbose mode
- Harden scheduler/statistics update behavior:
  - Add single-flight protection for overlapping statistics updates
  - Coalesce overlapping `days_back` requests to the larger window
  - Queue a bounded rerun when a larger `days_back` request arrives mid-flight
  - Keep incremental backfill decisions UTC-safe across DST boundaries
- Add persistent debug lifecycle controls to the add-on:
  - New `debug_auto_disable_hours` option (default `0` = disabled)
  - Persist debug state under `/data/debug_state.json`
  - Auto-disable debug logging at runtime without restart
  - Preserve auto-disable state across restart until operator explicitly re-arms debug
  - Add `GET /debug-status` for programmatic state inspection
- Expand runbook and release-process documentation:
  - Failure-category remediation matrix
  - Artifact retrieval/listing flow
  - Post-stabilization debug-disable procedure
  - Rollback procedure and pre-release verification checklist

## 2.5.1.3

- Fix add-on login result classification for reCAPTCHA challenge edge case:
  - When login remains on the login page with `recaptcha_iframe=True` and no captured login API response, classify as CAPTCHA-required instead of generic login failure
  - Integration now reports `captcha_required` for this path (instead of `invalid_credentials`)
  - Improves troubleshooting accuracy and retry behavior for invisible reCAPTCHA blocks

## 2.5.1.2

- Complete Phase C/E/F robustness work:
  - Configurable CAPTCHA auto-retry policy in integration options:
    - `captcha_auto_retry_count` (0 disables)
    - `captcha_auto_retry_delays_minutes` (comma-separated delays)
  - Configurable cookie-expiry warning threshold:
    - `expiry_warning_threshold_percent` (0 disables warnings)
  - Incremental/bounded backfill window based on `last_successful_datapoint_at` gap:
    - Trigger at 24h gap
    - Cap at 30 days
    - Applied to scheduled updates and post-refresh statistics updates
- Harden CAPTCHA retry lifecycle:
  - Prevent retry-loop self-rescheduling
  - Cancel pending CAPTCHA retry task on successful refresh
- Fix proactive refresh fallback path:
  - If proactive refresh fails, continue normal auth probe/update flow instead of exiting early
- Preserve existing options when fields are omitted in options flow:
  - Keep existing `proactive_refresh_max_age_hours` value when not resubmitted
- Improve add-on profile state accuracy:
  - Do not record profile creation when profile directory does not exist

## 2.5.1.1

- Add add-on debug logging toggle in Home Assistant add-on Configuration UI (`debug: bool`), with runtime log-level control in the add-on process
- Add configurable automation add-on base URL (`addon_url`) in integration setup/options flow
  - Use configured URL for add-on health checks and `/login` refresh calls
  - Persist URL with options precedence over entry data and default fallback
- Expand add-on connectivity diagnostics with explicit request URL/status and exception class in logs
- Fix refresh observability signals on unexpected refresh exceptions so status reports `failed` with fallback category `unknown_runtime_error`

## 2.5.1

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
- Add Home Assistant diagnostics hook with config-entry redaction and signal snapshot export

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
