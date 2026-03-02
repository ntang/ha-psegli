# Home Assistant OS Cutover Guide: Legacy Build -> Robustness Overhaul

## Who This Is For

Use this guide if your Home Assistant OS instance is currently running the older/broken PSEG setup and you want to move to the new architecture on `main`.

## What Changed (Critical)

The authentication path changed from the old multi-hop flow to direct login at:
- `https://mysmartenergy.psegliny.com/Dashboard`

Key operational changes:
- `enter_mfa_code` service is removed
- Cookie-based runtime auth remains (`MM_SID` + `__RequestVerificationToken`)
- Add-on performs automated login/refresh attempts
- Scheduled cookie validity checks run at `XX:00` and `XX:30`

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
- Home Assistant -> `Settings -> System -> Backups`
- Create a full backup before changing add-ons/integrations

Validation (must pass):
- Backup appears in backup list with current timestamp
- Backup status is complete (not in-progress/failed)

### Step 0.2 - Inventory old automations/scripts using removed service

Action:
- Search automations/scripts for `psegli.enter_mfa_code`
- Remove or disable those references now (service no longer exists)

Validation (must pass):
- No active automation/script still calling `psegli.enter_mfa_code`

---

## Phase 1: Update Add-on Source and Install New Add-on Build

### Step 1.1 - Ensure repository points to current project

Action:
- Home Assistant -> `Settings -> Add-ons -> Add-on Store -> ... -> Repositories`
- Ensure this repository exists:
  - `https://github.com/ntang/ha-psegli`
- Remove old/incorrect repo entries if needed

Validation (must pass):
- `PSEG Long Island Automation` appears in Add-on Store from this repo

### Step 1.2 - Install or update the add-on

Action:
- Open `PSEG Long Island Automation`
- Click `Install` (or `Update` if already installed)
- Start add-on
- Enable `Start on boot` (recommended)

Validation (must pass):
- Add-on state is `Running`
- Add-on logs show clean startup (no repeated crash loop)

### Step 1.3 - Verify add-on health endpoint

Action:
- Check add-on health endpoint:
  - `http://<HA_HOST>:8000/health`

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

Validation (must pass):
- Integration files on disk are updated
- No duplicate old copy remains in another path

### Step 2.2 - Restart Home Assistant

Action:
- Restart HA fully after integration update

Validation (must pass):
- HA restart completes normally
- No startup exception loop for `custom_components.psegli`

---

## Phase 3: Reconfigure Integration for New Auth Flow

### Step 3.1 - Re-open integration config and force fresh cookie path

Action:
- Home Assistant -> `Settings -> Devices & Services -> PSEG Long Island -> Configure`
- Confirm `username` and `password` are correct
- Clear `cookie` (leave blank) and submit

Why:
- This forces cookie retrieval through current add-on login flow

Validation (must pass):
- Config save succeeds OR shows temporary `captcha_required` prompt (see CAPTCHA playbook below)

### Step 3.2 - Confirm services are registered

Action:
- Developer Tools -> Actions
- Confirm both services exist:
  - `psegli.refresh_cookie`
  - `psegli.update_statistics`

Validation (must pass):
- Both services are available
- `psegli.enter_mfa_code` is not expected and should be absent

---

## Phase 4: Functional Validation (Required)

### Step 4.1 - Force cookie refresh manually

Action:
- Call service `psegli.refresh_cookie`

Validation (must pass):
- No service-call exception in UI/logs
- Logs indicate successful cookie refresh OR explicit `captcha_required` notice

### Step 4.2 - Force data update manually

Action:
- Call service `psegli.update_statistics` with:
```yaml
days_back: 0
```

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
- Auth migration background: `docs/auth-overhaul.md`
- Full overhaul plan and commit history: `docs/plans/2026-03-01-robustness-overhaul-plan.md`
