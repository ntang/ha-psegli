# Home Assistant OS Cutover Guide: Legacy Build -> Robustness Overhaul

## Who This Is For

Use this guide if your Home Assistant OS instance is currently running the older/broken PSEG setup and you want to move to the new architecture on `main`.

UI notes:
- Replace `<HA_HOST>` with your host (for example `homeassistant.local`).
- Default HA URL is usually `http://<HA_HOST>:8123`.
- Path examples in this guide can be opened directly in a browser:
  - Backups: `http://<HA_HOST>:8123/config/backups`
  - Automations: `http://<HA_HOST>:8123/config/automation/dashboard`
  - Scripts: `http://<HA_HOST>:8123/config/script/dashboard`
  - Developer Tools Actions: `http://<HA_HOST>:8123/developer-tools/action`

## What Changed (Critical)

The authentication path changed from the old multi-hop flow to direct login at:
- `https://mysmartenergy.psegliny.com/Dashboard`

Key operational changes:
- `enter_mfa_code` service is removed
- Cookie-based runtime auth remains (`MM_SID` + `__RequestVerificationToken`)
- Add-on performs automated login/refresh attempts
- Scheduled cookie validity checks run at `XX:00` and `XX:30`

## Upgrade Note (Legacy Broken Entry -> v2.5.0.4)

If you are upgrading from an older broken install, update to at least `v2.5.0.4` before troubleshooting auth.

Why this matters:
- Older builds can leave a legacy config entry that fails when opening Configure/Reconfigure.
- Symptom in logs:
  - `AttributeError: property 'config_entry' of 'PSEGLIOptionsFlow' object has no setter`
- UI symptom:
  - Integration card shows `Failed to set up: Check the logs`
  - Configure/Reconfigure does not present usable `username` / `password` flow

Required recovery sequence:
1. Update add-on + integration to `v2.5.0.4` or later.
2. Restart Home Assistant.
3. Delete the existing `PSEG Long Island` integration entry.
4. Restart Home Assistant again.
5. Add `PSEG Long Island` integration from scratch and enter `username` / `password`.
6. Leave `cookie` blank on first setup so add-on login path is used.

Validation after recovery:
- Initial setup form includes username/password fields.
- No options-flow `config_entry` setter exception appears in logs.
- `psegli.refresh_cookie` and `psegli.update_statistics` are callable in Developer Tools.

## Success Criteria

A successful cutover means all of the following are true:
1. Add-on is installed, running, and healthy
2. Integration is loaded without auth errors
3. `psegli.refresh_cookie` succeeds
4. `psegli.update_statistics` succeeds
5. Energy usage data continues updating in HA statistics

---

## Phase 0: Prepare and Protect

### Step 0.1 - Create a full Home Assistant backup

Action:
1. Open `Settings -> System -> Backups` (or `http://<HA_HOST>:8123/config/backups`).
2. Click `Create backup`.
3. Select `Full backup`.
4. Enter a name, for example: `pre-psegli-cutover-YYYY-MM-DD`.
5. Click `Create`.

Validation (must pass):
- Backup appears in backup list with current timestamp
- Backup status is complete (not in-progress/failed)

### Step 0.2 - Inventory old automations/scripts using removed service

Action:
1. Open `Settings -> Automations & Scenes -> Automations`
   (or `http://<HA_HOST>:8123/config/automation/dashboard`).
2. Open each automation that might reference PSEG actions.
3. Click the automation menu (`...`) -> `Edit in YAML`.
4. Search within the YAML editor for:
   - `psegli.enter_mfa_code`
   - `service: psegli.enter_mfa_code`
5. Remove that action or disable the automation until updated.
6. Repeat for scripts:
   - `Settings -> Automations & Scenes -> Scripts`
   - or `http://<HA_HOST>:8123/config/script/dashboard`

YAML-mode alternative:
1. Open `/config/automations.yaml` and `/config/scripts.yaml`.
2. Search for `psegli.enter_mfa_code`.
3. Remove those calls and reload automations/scripts.

Validation (must pass):
- No active automation/script still calling `psegli.enter_mfa_code`

---

## Phase 1: Update Add-on Source and Install New Add-on Build

### Step 1.1 - Ensure repository points to current project

Action:
1. Open `Settings -> Apps -> Install app`.
2. Click menu `...` (top-right) -> `Repositories`.
3. Ensure this repository exists:
  - `https://github.com/ntang/ha-psegli`
4. Remove old/incorrect repo entries if needed.
5. Click `Add` / `Save`, then refresh the Install app view.

Validation (must pass):
- `PSEG Long Island Automation` appears in Install app from this repo

### Step 1.2 - Install or update the add-on

Action:
1. Open `PSEG Long Island Automation` in Install app.
2. Click `Install` (or `Update` if already installed).
3. Open the `Info` tab and click `Start`.
4. Toggle `Start on boot` to enabled.
5. Open the `Log` tab and confirm startup completes.

Validation (must pass):
- Add-on state is `Running`
- Add-on logs show clean startup (no repeated crash loop)

### Step 1.3 - Verify add-on health endpoint

Action:
- Optional external/operator check of add-on health endpoint:
  - `http://<HA_HOST>:8000/health`
- Note: the integration itself checks the add-on internally at
  `http://localhost:8000/health` from inside Home Assistant.

Expected response:
```json
{"status":"healthy","service":"psegli-automation"}
```

Validation (must pass):
- Health endpoint returns HTTP 200 with `status=healthy`

---

## Phase 2: Update Integration

### Step 2.1 - Update `custom_components/psegli`

Action:
- If using HACS: update integration to latest `main`
- If manual: replace `/config/custom_components/psegli` with latest folder from this repo
- Ensure installed integration version is `2.5.0.4` or newer

Validation (must pass):
- Integration files on disk are updated
- No duplicate old copy remains in another path
- `custom_components/psegli/manifest.json` shows version `2.5.0.4` or newer

### Step 2.2 - Restart Home Assistant

Action:
1. Open `Settings -> System -> Restart` (or power menu in top-right).
2. Click `Restart Home Assistant`.
3. Wait for UI to reconnect and dashboards to load.

Validation (must pass):
- HA restart completes normally
- No startup exception loop for `custom_components.psegli`

---

## Phase 3: Reconfigure Integration for New Auth Flow

### Step 3.0 - Legacy entry recovery (if credentials are missing in UI)

Use this step if any of the following are true:
- PSEG entry shows `Failed to set up` and `Configure/Reconfigure` does not show usable credential fields
- You migrated from an older build and the entry appears partially broken
- Logs include options-flow errors such as:
  - `AttributeError: property 'config_entry' ... has no setter`

Action:
1. Open `Settings -> Devices & Services`.
2. Open the `PSEG Long Island` integration card.
3. Click menu `...` -> `Delete`.
4. Restart Home Assistant.
5. Add integration again:
   - `Settings -> Devices & Services -> Add Integration -> PSEG Long Island`
6. Enter fresh `username` and `password` during initial setup.
7. Leave `cookie` empty first to allow add-on retrieval.

Validation (must pass):
- New entry is created successfully
- Initial setup form includes username/password fields
- No options-flow `config_entry` setter error appears in logs

### Step 3.1 - Reconfigure (Options flow) and force fresh cookie path

Action:
- Home Assistant -> `Settings -> Devices & Services -> PSEG Long Island -> Reconfigure/Configure`
- Clear `cookie` (leave blank) and submit
- If username/password are wrong, remove and re-add the integration
  (credentials are set in the initial config flow, not options flow).

Why:
- This forces cookie retrieval through current add-on login flow

Validation (must pass):
- Config save succeeds OR shows temporary `captcha_required` prompt (see CAPTCHA playbook below)

### Step 3.2 - Confirm services are registered

Action:
1. Open `Developer Tools -> Actions`
   (or `http://<HA_HOST>:8123/developer-tools/action`).
2. In the `Action` selector, confirm both services exist:
  - `psegli.refresh_cookie`
  - `psegli.update_statistics`
3. Confirm `psegli.enter_mfa_code` does not exist.

Validation (must pass):
- Both services are available
- `psegli.enter_mfa_code` is not expected and should be absent

---

## Phase 4: Functional Validation (Required)

### Step 4.1 - Force cookie refresh manually

Action:
1. Open `Developer Tools -> Actions`.
2. Select action `psegli.refresh_cookie`.
3. Leave data empty.
4. Click `Perform action`.

Validation (must pass):
- No service-call exception in UI/logs
- Logs indicate successful cookie refresh OR explicit `captcha_required` notice

### Step 4.2 - Force data update manually

Action:
1. Open `Developer Tools -> Actions`.
2. Select action `psegli.update_statistics`.
3. Enter action data:
```yaml
days_back: 0
```
4. Click `Perform action`.

Validation (must pass):
- Service call completes
- Logs show statistics update started/completed
- New/updated usage statistics visible in Energy dashboard/statistics views

### Step 4.3 - Validate scheduler behavior

Action:
- Let system run through at least one scheduled checkpoint (`XX:00` or `XX:30`)

Validation (must pass):
- Logs show scheduled check activity
- If cookie valid: refresh skipped and updates continue
- If cookie expired: refresh attempt executed

---

## CAPTCHA Handling Playbook (When It Triggers)

Important constraint:
- In Home Assistant OS add-on mode, login is headless. There is no built-in interactive CAPTCHA UI in HA to click through.
- The intended mechanism is retries + persistent browser profile trust.

### Path A (preferred): Retry with persistent profile

Action:
1. Run `psegli.refresh_cookie`
2. If `captcha_required`, wait 30-120 seconds
3. Retry 2-5 times
4. Keep add-on installed/running (do not repeatedly reinstall/reset profile)

Validation (must pass):
- One retry eventually returns a valid cookie and refresh succeeds

### Path B (fallback): Manual cookie injection

Use if Path A repeatedly fails.

Action:
1. In a normal desktop browser, log in to:
   - `https://mysmartenergy.psegliny.com/Dashboard`
2. Open browser dev tools -> Application/Storage -> Cookies for `mysmartenergy.psegliny.com`
3. Copy values for:
   - `MM_SID`
   - `__RequestVerificationToken`
4. Build cookie string exactly:
   - `MM_SID=<value>; __RequestVerificationToken=<value>`
5. Home Assistant -> PSEG Integration -> Configure
6. Paste this cookie string into `cookie` and submit

Validation (must pass):
- Config save succeeds
- `psegli.update_statistics` succeeds immediately after

Script-assisted alternative:
- If browser devtools extraction is inconvenient, use the local script workflow in
  [`docs/cookie-login-playbook.md`](docs/cookie-login-playbook.md) to generate and
  validate a cookie string, then paste it into HA.

---

## Post-Cutover Monitoring (First 48 Hours)

### Step 5.1 - Monitor logs for auth stability

Action:
- Watch `custom_components.psegli` and add-on logs for 24-48h

Validation (must pass):
- No repeated auth-disable loop
- No persistent CAPTCHA deadlock

### Step 5.2 - Confirm periodic updates

Action:
- Verify multiple data updates across at least one day

Validation (must pass):
- Statistics continue to append over time
- Scheduled checks occur at expected times

### Step 5.3 - Check cookie-age observability logs

Action:
- Look for cookie age log entries during scheduled checks/expiration paths

Validation (must pass):
- Cookie age logs appear when relevant (used for refresh-tuning decisions)

---

## Rollback Plan (If Cutover Fails)

If any critical validation step fails and you need immediate recovery:
1. Stop new add-on/integration changes
2. Restore the full HA backup created in Step 0.1
3. Confirm services/data return to pre-cutover state

---

## References

- Overview: `README.md`
- Installation: `INSTALLATION.md`
- Cookie/login playbook: `docs/cookie-login-playbook.md`
- Auth migration background: `docs/auth-overhaul.md`
- Full overhaul plan and commit history: `docs/plans/2026-03-01-robustness-overhaul-plan.md`
