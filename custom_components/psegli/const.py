"""Constants for the PSEG Long Island integration."""

DOMAIN = "psegli"

# Configuration
CONF_COOKIE = "cookie"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_ADDON_URL = "addon_url"

# Observability options (Phase 3.3)
CONF_DIAGNOSTIC_LEVEL = "diagnostic_level"
CONF_NOTIFICATION_LEVEL = "notification_level"

DIAGNOSTIC_STANDARD = "standard"
DIAGNOSTIC_VERBOSE = "verbose"
NOTIFICATION_CRITICAL_ONLY = "critical_only"
NOTIFICATION_VERBOSE = "verbose"

# CAPTCHA auto-retry (Phase C)
CONF_CAPTCHA_AUTO_RETRY_COUNT = "captcha_auto_retry_count"
CONF_CAPTCHA_AUTO_RETRY_DELAYS_MINUTES = "captcha_auto_retry_delays_minutes"
DEFAULT_CAPTCHA_AUTO_RETRY_COUNT = 2
DEFAULT_CAPTCHA_AUTO_RETRY_DELAYS_MINUTES = [5, 15]

# Backward-compatible aliases used in older tests/callers
CAPTCHA_AUTO_RETRY_COUNT = DEFAULT_CAPTCHA_AUTO_RETRY_COUNT
CAPTCHA_AUTO_RETRY_DELAYS_MINUTES = DEFAULT_CAPTCHA_AUTO_RETRY_DELAYS_MINUTES
FIRST_START_GRACE_RETRIES = 2
FIRST_START_GRACE_DELAY_SECONDS = 15

# Proactive refresh (Phase E)
CONF_PROACTIVE_REFRESH_MAX_AGE_HOURS = "proactive_refresh_max_age_hours"
DEFAULT_PROACTIVE_REFRESH_MAX_AGE_HOURS = 20
CONF_EXPIRY_WARNING_THRESHOLD_PERCENT = "expiry_warning_threshold_percent"
DEFAULT_EXPIRY_WARNING_THRESHOLD_PERCENT = 80

# Backward-compatible alias
EXPIRY_WARNING_THRESHOLD_PERCENT = DEFAULT_EXPIRY_WARNING_THRESHOLD_PERCENT

# Incremental fetch / bounded backfill (Phase F)
DEFAULT_AUTO_BACKFILL_TRIGGER_HOURS = 24
DEFAULT_MAX_AUTO_BACKFILL_DAYS = 30

# Addon configuration
ADDON_SLUG = "psegli-automation"
DEFAULT_ADDON_URL = "http://localhost:8000"
# Internal options key: True when addon_url was auto-discovered (not user-set)
OPTION_ADDON_URL_AUTO = "_addon_url_auto"
