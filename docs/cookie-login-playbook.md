# PSEG Cookie and Login Playbook

Use this document when the integration needs a manual cookie, or when you want
to generate/validate cookies with local scripts before entering them in Home
Assistant.

## Choose a Path

1. Preferred: keep the add-on running and let `psegli.refresh_cookie` handle it.
2. Fallback A: copy cookies from your browser session.
3. Fallback B: use repo scripts to generate/test cookie strings, then paste.

## Cookie Format Required by Integration

Use exactly:

```text
MM_SID=<MM_SID_VALUE>; __RequestVerificationToken=<TOKEN_VALUE>
```

Rules:
- Include both names exactly: `MM_SID` and `__RequestVerificationToken`
- Keep separator as `; `
- Do not wrap values in quotes

## Fallback A: Browser Cookie Extraction

1. Open `https://mysmartenergy.psegliny.com/Dashboard` in a desktop browser.
2. Sign in fully.
3. Open developer tools:
- Chrome/Edge: `Cmd+Option+I` (Mac) or `F12`
- Firefox: `Cmd+Option+I` (Mac) or `F12`
- Safari: enable Developer menu, then `Develop -> Show Web Inspector`
4. Open site cookies:
- Chrome/Edge: `Application -> Storage -> Cookies -> https://mysmartenergy.psegliny.com`
- Firefox: `Storage -> Cookies -> https://mysmartenergy.psegliny.com`
- Safari: `Storage -> Cookies -> mysmartenergy.psegliny.com`
5. Copy `MM_SID` and `__RequestVerificationToken`.
6. Build the cookie string using the required format above.

## Fallback B: Script-Assisted Cookie Generation (Local Machine)

Run this on your laptop/workstation clone of this repo, not inside HA UI.

### Prerequisites

1. Repo checked out locally.
2. Python environment with `addons/psegli-automation/requirements.txt` installed.
3. Playwright browser dependencies installed.

### Generate Cookie via Login Script

```bash
cd addons/psegli-automation
python test_direct_login_v2.py "<EMAIL>" "<PASSWORD>"
```

Expected:
- Script attempts login through Playwright.
- On success, cookie string is saved to:
  - `addons/psegli-automation/.cookie`

### Validate Saved Cookie

```bash
cd addons/psegli-automation
python test_cookie_validity.py
```

Expected:
- Script reports cookie is valid against dashboard/API endpoints.

### Extract Cookie from Existing Persistent Profile

If login already happened before, you can try:

```bash
cd addons/psegli-automation
python extract_and_test_cookie.py
```

Expected:
- Extracts `MM_SID` + `__RequestVerificationToken` from `.browser_profile`
- Tests validity immediately

## Enter Cookie in Home Assistant

New integration setup:
1. `Settings -> Devices & Services -> Add Integration -> PSEG Long Island`
2. Enter username/password.
3. Paste cookie string into `cookie`.
4. Submit.

Existing integration:
1. `Settings -> Devices & Services -> Integrations -> PSEG Long Island`
2. Open `Configure` or `Options` (label varies by HA version).
3. Paste cookie string into `cookie`.
4. Submit.

If `Configure`/`Options` is missing:
1. Delete failed PSEG integration entry.
2. Restart Home Assistant.
3. Re-add integration and provide credentials/cookie in initial setup flow.

## Validate End-to-End

1. `Settings -> Devices & Services -> PSEG Long Island` shows loaded (not failed).
2. `Developer Tools -> Actions` includes:
- `psegli.refresh_cookie`
- `psegli.update_statistics`
3. Run `psegli.update_statistics` with:

```yaml
days_back: 0
```

4. Check HA core logs (`Settings -> System -> Logs`) for no new
   `custom_components.psegli` auth errors.

## Security Handling

- Treat cookie strings as credentials.
- Do not paste cookie values into tickets, chat logs, or screenshots.
- Regenerate cookie if exposed.
