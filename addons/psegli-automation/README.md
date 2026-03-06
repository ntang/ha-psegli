# PSEG Long Island Automation Addon

This Home Assistant addon provides automated login services for PSEG Long Island using Playwright. It runs in its own container and exposes a web API for cookie generation.

**Version**: 2.5.1.3

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

### Debug Logging Toggle

Addon options include a `debug` boolean:

- `debug: false` (default) keeps normal log volume.
- `debug: true` enables verbose add-on logging for troubleshooting.

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
