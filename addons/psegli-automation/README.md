# PSEG Long Island Automation Addon

This Home Assistant addon provides automated login services for PSEG Long Island using Playwright. It runs in its own container and exposes a web API for cookie generation.

**Version**: 2.5.2.1

## Features

- 🚀 **Automated Login**: Uses Playwright to handle reCAPTCHA and login
- 🔐 **Cookie Generation**: Returns fresh authentication cookies
- 🌐 **Web API**: Simple HTTP endpoints for integration use
- 🐳 **Docker-based**: Runs in isolated container with all dependencies
- 📱 **Home Assistant Integration**: Works seamlessly with PSEG Long Island integration

## Installation

### **Option 1: Repository Installation (Recommended)**

1. **Add the custom repository:**

   - Go to **Settings** → **Apps** → **Install app**
   - Click the three dots menu (⋮) → **Repositories**
   - Add: `https://github.com/ntang/ha-psegli`
   - Click **Add**

2. **Install the addon:**
   - Find **PSEG Long Island Automation** in the store
   - Click **Install**
   - Wait for installation to complete
   - Click **Start**

### **Option 2: Local Installation**

1. **Copy to Addons Directory**: Copy this folder to your Home Assistant `addons` directory
2. **Install Addon**: Go to **Settings** → **Apps** → **Install app** (local app appears there)
3. **Start Addon**: Click "Install" then "Start"

## API Endpoints

### Health Check

```
GET /health
```

### Login (JSON)

```
POST /login
Content-Type: application/json

{
  "username": "your_email@example.com",
  "password": "your_password"
}
```

### Login (Form Data)

```
POST /login-form
Content-Type: application/x-www-form-urlencoded

username=your_email@example.com&password=your_password
```

## Response Format

```json
{
  "success": true,
  "cookies": "MM_SID=abc123; __RequestVerificationToken=xyz789; ..."
}
```

On failure:

```json
{
  "success": false,
  "error": "Login failed",
  "captcha_required": true
}
```

## Integration Usage

The PSEG Long Island integration will automatically use this addon when available. No additional configuration needed.

## reCAPTCHA Handling

The addon logs in directly to mysmartenergy.psegliny.com (no separate Okta/MFA step in this flow), which uses Google invisible reCAPTCHA. The addon handles this by:

- Using `playwright-stealth` for anti-fingerprinting
- Maintaining a persistent browser profile (`.browser_profile/`) to build reCAPTCHA trust over time
- Returning `captcha_required: true` if a visible challenge is triggered

reCAPTCHA challenges usually stop appearing after a few successful logins with the persistent profile.

## Troubleshooting

- **Port Conflicts**: Ensure port 8000 is available
- **Browser Issues**: Check addon logs for Playwright errors
- **Network Issues**: Verify addon can reach PSEG website
- **reCAPTCHA**: If login fails with `captcha_required`, retry — the persistent profile builds trust over time

### Failure Category Remediation

When login fails, the add-on returns a `category` field indicating the type of failure. Use this matrix to determine the correct remediation:

| Category | Meaning | Remediation |
|----------|---------|-------------|
| `invalid_credentials` | Username/password rejected by PSEG site | Verify credentials in integration config; re-enter if changed |
| `captcha_required` | reCAPTCHA challenge triggered | Retry automatically (integration has auto-retry); persistent profile builds trust over time |
| `addon_disconnect` | Transport disconnected mid-login | Check add-on logs; verify network stability; retry |
| `addon_unreachable` | Cannot reach add-on endpoint | Verify add-on is running; check `addon_url` in integration options; check port 8000 |
| `transient_site_error` | Upstream PSEG site returned 5xx or transient error | Wait and retry; do NOT treat as auth failure; site will recover |
| `unknown_runtime_error` | Unexpected failure (fallback) | Check add-on logs for details; enable debug logging temporarily |

### Retrieving Login Failure Artifacts

The add-on persists failure artifacts (HTML snapshots, screenshots) under `/data/login_failures/` for post-mortem analysis. To list recent artifacts:

```
GET /artifacts/login-failures?limit=10
```

Response contains metadata only (no raw HTML/screenshot bytes):

```json
{
  "count": 2,
  "items": [
    {
      "id": "1741200000000",
      "created_at": "2026-03-05T20:00:00+00:00",
      "category": "unknown_runtime_error",
      "subreason": "site_flow_changed",
      "url": "https://mysmartenergy.psegliny.com/",
      "title": "MySmartEnergy",
      "recaptcha_iframe": false,
      "html_file": "1741200000000/page.html",
      "screenshot_file": "1741200000000/page.png"
    }
  ]
}
```

Retention: latest 10 artifacts are kept; older ones are pruned on startup and after each new artifact write.

### Post-Stabilization Debug Disable

After debugging is complete, reduce log volume:

1. **Manual:** Set `debug: false` in add-on configuration and restart.
2. **Automatic:** Set `debug_auto_disable_hours` to a non-zero value (e.g., `24`). Debug logging will revert to INFO after the specified hours — no restart needed. Check status via `GET /debug-status`.

### Debug Logging Toggle

Addon options include a `debug` boolean:

- `debug: false` (default) keeps normal log volume.
- `debug: true` enables verbose add-on logging for troubleshooting.

### Debug Auto-Disable

To prevent runaway log volume, the add-on supports automatic debug disabling:

- `debug_auto_disable_hours: 0` (default) — auto-disable is off; debug stays on until manually toggled.
- `debug_auto_disable_hours: 24` — debug logging automatically reverts to INFO after 24 hours.

The auto-disable state is persisted under `/data/debug_state.json` and survives add-on restarts. When the timer expires, the log level is changed at runtime via `setLevel()` — no restart required.

To check current debug state programmatically:

```
GET /debug-status
```

Returns:
```json
{
  "debug_enabled": true,
  "auto_disable_hours": 24,
  "debug_enabled_at": 1741200000.0,
  "auto_disable_at": 1741286400.0
}
```

## Development

To build locally:

```bash
docker build -t psegli-automation .
docker run -p 8000:8000 psegli-automation
```

### Watch login flow in headed mode (visible browser)

To see the browser during login for debugging, run the addon locally (not in Docker/HA) with headed mode:

```bash
cd addons/psegli-automation
HEADED=1 python run.py
```

Then in another terminal:
```bash
curl -X POST http://localhost:8000/login -H "Content-Type: application/json" -d '{"username":"your@email.com","password":"yourpass"}'
```

A browser window will open so you can watch the login and reCAPTCHA flow.
