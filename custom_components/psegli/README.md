# PSEG Long Island Integration (custom_components/psegli)

Home Assistant custom integration that ingests PSEG Long Island usage data into HA long-term statistics.

For full install instructions, use:
- [`../../INSTALLATION.md`](../../INSTALLATION.md)

## What This Integration Does

- Validates and stores PSEG auth cookie
- Fetches usage data from PSEG endpoints
- Writes on-peak/off-peak series into Home Assistant statistics
- Coordinates cookie refresh with the automation add-on

## Authentication Model

Current model is cookie-based at runtime.

- Setup accepts `username`, `password`, optional `cookie`.
- If cookie is omitted, integration attempts automated cookie retrieval via add-on.
- Cookie is validated before persistence.

Legacy MFA workflow has been removed from integration logic.

See auth migration notes:
- [`../../docs/auth-overhaul.md`](../../docs/auth-overhaul.md)

## Services

Defined in [`services.yaml`](services.yaml):

1. `psegli.update_statistics`
- Optional field: `days_back` (0-365)
- Use for manual updates/backfill

2. `psegli.refresh_cookie`
- Forces cookie refresh via add-on

## Scheduled Behavior

- Cookie validity checks run at `XX:00` and `XX:30`.
- If cookie is valid, refresh is skipped.
- If invalid, integration attempts add-on login and cookie replacement.

## Add-on Dependency

Add-on is strongly recommended for reliable operation.

Without add-on:
- Integration can still run with a manually provided cookie
- Cookie refresh/recovery must be handled manually

## Developer Notes

Run tests from repo root:

```bash
python -m pytest -q
```

Primary implementation files:
- [`__init__.py`](__init__.py) - setup, services, scheduler, refresh logic
- [`config_flow.py`](config_flow.py) - setup/options flows
- [`psegli.py`](psegli.py) - synchronous HTTP client and parsing
- [`auto_login.py`](auto_login.py) - add-on communication wrapper
