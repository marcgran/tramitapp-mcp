# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project purpose

MCP (Model Context Protocol) server that exposes the TramitApp HR platform API as tools consumable by Claude Desktop via **stdio transport**. TramitApp manages time-tracking, absences, shifts, documents and payroll. The token may access several companies (multi-company).

## Stack

- Python 3.10+, `mcp` SDK with `FastMCP` (`@mcp.tool()` decorators), `httpx` for async HTTP
- Single entry point: `server.py`
- Dependencies: `requirements.txt` (only `mcp>=1.2.0` and `httpx>=0.27.0`)

## Commands

```bash
# Setup (Windows)
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# Test server interactively with MCP inspector (opens web UI)
mcp dev server.py
```

No test suite, no build step, no linter configuration.

### Claude Desktop config

`C:\Users\USUARIO\AppData\Roaming\Claude\claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "tramitapp": {
      "command": "C:\\ruta\\a\\.venv\\Scripts\\python.exe",
      "args": ["C:\\ruta\\a\\tramitapp-mcp\\server.py"],
      "env": {
        "TRAMITAPP_API_TOKEN": "tu_token",
        "TRAMITAPP_AUTH_MODE": "header"
      }
    }
  }
}
```

Restart Claude Desktop after editing this file.

## Architecture

`server.py` is intentionally a single flat file with three layers:

| Layer | What it does |
|-------|-------------|
| `_auth_headers()` | Sole auth logic — never duplicate. Sends `auth: TOKEN` (no `Bearer` prefix). |
| `_http()` | Raw HTTP with uniform error handling. Returns `{"error": "..."}` dicts on failure; never raises. |
| `_request()` | What tools call. Resolves `{company_id}` in paths via `_empresa_id()` and delegates to `_http()`. |
| `_empresa_id()` | Multi-company resolution: `empresa` tool param (name or 24-hex `_id`) > `TRAMITAPP_EMPRESA_ID` default > sole company of the token. Names resolve against `GET /companies`, cached in `_EMPRESAS_CACHE` for the process lifetime. |
| `PATHS` dict | All API routes in one place — edit only here when routes change. |

Tools follow a strict pattern: call `_request()`, return the result. Every company-scoped tool takes an optional `empresa` param (company name or `_id`). Read tools return data; write tools are marked `[MODIFICA DATOS]` in their docstring. The API has no per-employee filter on list endpoints, so `_filtrar_por_empleado()` filters client-side by `employees_id`.

## API (confirmed against the official OpenAPI spec)

Spec: `docs/tramitapp-api.json` (downloaded from `https://rrhh.tramitapp.com/tramitapp-api.json`; human-readable Swagger at `/doc`).

- Real base path is **`/tramitapi`** (not `/api`).
- Almost every endpoint is scoped as `/tramitapi/{company_id}/...` — the company `_id` comes from `GET /tramitapi/companies` (tool `listar_empresas`); tools resolve it per-call from their `empresa` param (multi-company).
- **No pagination** anywhere. Employee list supports `modified_since`, `columns`, `include`.
- Date ranges use `start`/`end`: **days** (`YYYY-MM-DD`) for `absences`, **months** (`YYYY-MM`) for `hours` and `shifts`.
- Clockings: `POST /{company_id}/clocking` with `{employees_id, date, tz, in}` (auto) or `POST /{company_id}/hours` with `{employees_id, start_date, start_time, end_date, end_time}` (manual).
- `POST /{company_id}/documents` is a query (filters in body), not an upload.
- Also available (not all wrapped as tools yet): `expenses`, `vacations`, `schedules`, employee hire/fire/renew, absence/hour cancel, webhooks.

## Authentication

TramitApp uses a **custom header** (confirmed):
```
auth: TOKEN   ← no "Bearer" prefix
```

Env vars (all optional except `TRAMITAPP_API_TOKEN`):

| Variable | Default | Description |
|----------|---------|-------------|
| `TRAMITAPP_BASE_URL` | `https://rrhh.tramitapp.com` | API base URL |
| `TRAMITAPP_API_TOKEN` | *(empty)* | Auth token |
| `TRAMITAPP_AUTH_MODE` | `header` | `header` or `bearer` |
| `TRAMITAPP_AUTH_HEADER` | `auth` | Header name in `header` mode |
| `TRAMITAPP_TIMEOUT` | `30` | Seconds |
| `TRAMITAPP_EMPRESA_ID` | *(empty)* | optional default company (name or `_id`); each tool's `empresa` param overrides it |

**Demo token ≠ production token.** TramitApp issues a demo token first; do not hardcode it.

## Implemented tools

| Tool | Type |
|------|------|
| `listar_empresas` | read — companies the token can access; their names/`_id`s feed the `empresa` param |
| `listar_empleados` | read — full list (no pagination); `modified_since`/`columns`/`include` |
| `obtener_empleado` | read — by ID |
| `listar_fichajes` | read — month range `YYYY-MM` (`/hours`) |
| `listar_ausencias` | read — day range `YYYY-MM-DD` (`/absences`) |
| `listar_turnos` | read — month range `YYYY-MM` (`/shifts`) |
| `saldo_vacaciones` | read — vacation balances (`/vacations`) |
| `crear_fichaje` | **write** — `/clocking`; always test on demo first |

## Pending work

1. **Expand tools** as needed: employee hire/fire/renew, expenses, schedules, document queries, absence/hour cancel.
2. **Smoke-test `crear_fichaje`** in a controlled case (read endpoints are verified live against all 4 companies; the write endpoint is not).

## Conventions

- Docstrings and user-facing messages in **Spanish**.
- All new tools route through `_request()` — no custom error handling.
- Auth lives only in `_auth_headers()`; routes live only in `PATHS`.
