# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

An MCP (Model Context Protocol) server that exposes a DevOps platform (work-item / test-case management system) as tools consumable by LLM clients. The server authenticates against a DevOps HTTP API, then exposes tools for querying/creating work items, comments, status changes, and attachment handling.

The codebase and DevOps API use Chinese terminology (e.g. 故事/任务/bug/风险). Keep new tool descriptions and log messages bilingual-friendly or Chinese to match existing style.

## Tech Stack

- Python 3.13 (pinned in `.python-version`)
- Package manager: **uv** (lockfile: `uv.lock`, project spec: `pyproject.toml`)
- `mcp[cli]` — provides `FastMCP` server framework
- `httpx` — async HTTP client to the DevOps API
- `python-dotenv` + dataclass — config loading from `.env`
- `pydantic` — request models

## Common Commands

```bash
# Install / sync dependencies
uv sync

# Run the MCP server (listens via streamable-http transport)
uv run python main.py
```

There is currently **no test suite, linter, or formatter configured**. Do not invent test commands.

## Architecture

Layered structure with strict separation between MCP layer and HTTP layer:

```
main.py               → entry point: logging config, imports the tools package, mcp.run()
server.py             → shared FastMCP instance, app_lifespan (per-session DevOpsClient),
                       get_client(ctx), required/optional header-name constants
tools/
  __init__.py         → imports domain modules to trigger @mcp.tool registration
  workitems.py        → work-item tools (list/create/details/comment/status-change)
  attachments.py      → attachment tools (preview/chunk/resource) + preview helpers
  testcases.py        → test-case tools (group & case create/query) + Step model helpers
devops_client.py      → DevOps HTTP API wrapper; persistent httpx.AsyncClient + user info
config.py             → Config dataclass; loads DEVOPS_* vars from .env (legacy, not used at runtime)
```

**Key flow:** tool functions live in `tools/*.py` and register onto the shared `mcp` instance (from `server.py`) at import time; `main.py` merely imports the `tools` package and starts the server. Each tool is a thin adapter that:
1. Calls `get_client(ctx)` to obtain the session's `DevOpsClient`.
2. Calls the corresponding `DevOpsClient` method.
3. Reshapes the raw API JSON into a smaller dict (renaming/filtering fields).
4. Catches all exceptions and returns `{"error": "..."}` rather than raising — do not change this contract without reason, MCP clients depend on tool calls not throwing.

**Authentication:** the MCP client injects configuration via HTTP headers (`X-DevOps-Base-URL`, `X-DevOps-afcToken`, optional `X-DevOps-{Project,Iteration,Module,Version}-ID`). On the first tool call of a session, `get_client` builds a `DevOpsClient` and validates the token via `verify_token()` (`GET /api/devops/uc/users/current-user`), caching a `UserInfo`. Client methods that read `self._user_info` rely on this session-level verification. `DevOpsClient` holds one long-lived `httpx.AsyncClient` (connection pooling); `app_lifespan` calls `aclose()` when the session ends.

**Work-item type mapping** appears in two places and they are **not identical** — keep them in sync when changing types:
- `devops_client.py` `workitem_type_map` — keyed by human-friendly names (`story`/`task`/`bug`/`risk`), used for **creating** items. Maps to `{workitemTypeId, workitemTypeName}`.
- `tools/workitems.py` `workitem_type` — keyed by numeric DevOps type ID (`2`/`3`/`4`/`5`), used for **reading/decoding** queried items.

IDs: `2`=故事/user-story, `3`=任务/task, `4`=bug, `5`=风险/risk.

**Priority conversion:** `devops_client.priority_convert` maps `P0`–`P4` to `highest`/`high`/`medium`/`low`/`lowest` (unknown values fall back to `"1"`).

**Work-hour conversion:** `man_hour_convert` multiplies input hours by 3600 (DevOps API expects seconds).

## Adding a New MCP Tool

1. Add the underlying HTTP method to `DevOpsClient` in `devops_client.py`. Follow the existing pattern: build the URL from `self.base_url`, call `self.get/post/put`, and `return r.json()` (or the parsed shape). Methods may read `self._user_info` — it is populated by `verify_token()` during session construction.
2. Add an `@mcp.tool(...)` adapter in the matching domain module under `tools/` (for a new domain, create the module and import it in `tools/__init__.py`). Use a concise `description=` (this is what the LLM sees). Wrap the body in try/except and return `{"error": ...}` on failure.
3. When reshaping API responses, only expose the fields a client needs — existing tools deliberately drop most raw fields.

## Configuration & Environment

Required environment variables (loaded by `Config.from_env` in `config.py`). Copy `.env.example` style — the `.env` file in this repo contains real credentials and is **not** gitignored (verify before committing). `Config.from_env` raises `ValueError` if `DEVOPS_BASE_URL`, `DEVOPS_USERNAME`, or `DEVOPS_PASSWORD` are missing; the four ID vars default to empty string.

| Variable | Purpose | Required |
|---|---|---|
| `DEVOPS_BASE_URL` | DevOps instance root (e.g. `http://localhost:14080`) | yes |
| `DEVOPS_USERNAME` / `DEVOPS_PASSWORD` | Login credentials for `/api/uc/users/login` | yes |
| `DEVOPS_PROJECT_ID` | Default project for new work items | no |
| `DEVOPS_ITERATION_ID` | Default iteration (sprint) for new work items | no |
| `DEVOPS_MODULE_ID` | Default module for new work items | no |
| `DEVOPS_VERSION_ID` | Default version for new work items | no |

The four ID vars are baked into the `DevOpsClient` instance at startup and used as defaults inside `create_workitem` / `create_testcases`. They are **not** per-call parameters — changing them requires editing `.env` and restarting the server.

## Transport

Server runs with `transport="streamable-http"` (see `main.py::main`). There is no CLI/stdio transport configured.

## Known Gotchas

- `get_workitem_list` has a typo in its output dict (now lives in `tools/workitems.py`): `"prmoduleIdiority"` maps to `moduleId`. Preserve existing field names when modifying unless explicitly renaming across all consumers.
- Error contract: list-returning tools (`get_workitem_list`, `get_testcase_groups`) return `{"error": ...}` (a dict) on failure instead of their normal list — do not reintroduce `[{"error": ...}]`.
- `client.get_project()` exists in `devops_client.py` but no MCP tool exposes it.
- Tool registration happens at import time: a new `tools/` domain module must be imported in `tools/__init__.py`, otherwise its tools never register.
