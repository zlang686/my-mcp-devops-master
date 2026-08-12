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

Three-file structure with strict separation between MCP layer and HTTP layer:

```
main.py          → MCP server: defines @mcp.tool functions, shapes request/response dicts
devops_client.py → DevOps HTTP API wrapper; owns auth token + user info lifecycle
config.py        → Config dataclass; loads DEVOPS_* vars from .env (via python-dotenv)
```

**Key flow:** `main.py` constructs a single `Config` and a single `DevOpsClient` at import time. Each `@mcp.tool` function is a thin adapter that:
1. Calls the corresponding `DevOpsClient` method.
2. Reshapes the raw API JSON into a smaller dict (renaming/filtering fields).
3. Catches all exceptions and returns `{"error": "..."}` rather than raising — do not change this contract without reason, MCP clients depend on tool calls not throwing.

**Authentication:** `DevOpsClient` lazily logs in (`POST /api/uc/users/login`) on the first request that lacks a token. On success it caches `_token` and a `UserInfo` instance. Public methods (`get`/`post`/`put`) and the mutation helpers (`create_workitem`, `create_testcases`) call `await self.login()` if `_token` is falsy — preserve this lazy-init pattern when adding new endpoints that read `self._user_info`.

**Work-item type mapping** appears in two places and they are **not identical** — keep them in sync when changing types:
- `devops_client.py` `workitem_type_map` — keyed by human-friendly names (`story`/`task`/`bug`/`risk`), used for **creating** items. Maps to `{workitemTypeId, workitemTypeName}`.
- `main.py` `workitem_type` — keyed by numeric DevOps type ID (`2`/`3`/`4`/`5`), used for **reading/decoding** queried items.

IDs: `2`=故事/user-story, `3`=任务/task, `4`=bug, `5`=风险/risk.

**Priority conversion:** `devops_client.priority_convert` maps `P0`–`P4` to `highest`/`high`/`medium`/`low`/`lowest` (unknown values fall back to `"1"`).

**Work-hour conversion:** `man_hour_convert` multiplies input hours by 3600 (DevOps API expects seconds).

## Adding a New MCP Tool

1. Add the underlying HTTP method to `DevOpsClient` in `devops_client.py`. Follow the existing pattern: build the URL from `self.base_url`, call `self.get/post/put`, and `return r.json()` (or the parsed shape). Reuse the lazy `await self.login()` guard if you need `self._user_info`.
2. Add an `@mcp.tool(...)` adapter in `main.py` with a one-line `description=` (this is what the LLM sees). Wrap the body in try/except and return `{"error": ...}` on failure.
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

- `add_workitem_comment` and `change_workitem_status` return `r.json()` on the **already-consumed** `httpx.Response` — `DevOpsClient.post` returns the response object, and the tool calls `.json()` once. Do not call `.json()` twice on the same response.
- `get_workitem_list` has a typo in its output dict: `"prmoduleIdiority"` maps to `moduleId`. Preserve existing field names when modifying unless explicitly renaming across all consumers.
- `create_testcase` in `main.py` is **not** decorated with `@mcp.tool` — it is dead/unfinished code (uses `TypeAdapter(List[Step]).dump_json`). Check before wiring it up.
- `get_project` tool is commented out in `main.py`; the underlying `client.get_project()` still exists.
