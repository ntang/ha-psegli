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

# Addon configuration
ADDON_SLUG = "psegli-automation"
DEFAULT_ADDON_URL = "http://localhost:8000"
