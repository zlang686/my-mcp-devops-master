# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

An MCP (Model Context Protocol) server that exposes a DevOps platform (work-item / test-case management system) as tools consumable by LLM clients. The server authenticates against a DevOps HTTP API, then exposes tools for querying/creating work items, comments, status changes, and attachment handling.

The codebase and DevOps API use Chinese terminology (e.g. 故事/任务/bug/风险). Keep new tool descriptions and log messages bilingual-friendly or Chinese to match existing style.

## Tech Stack

- Python 3.13 (pinned in `.python-version`)
- Package manager: **uv** (lockfile: `uv.lock`, project spec: `pyproject.toml`)
- `mcp[cli]>=2.0.0,<3` — provides the v2 `MCPServer` framework (`from mcp.server.mcpserver import MCPServer, Context`; `mcp.server.fastmcp` no longer exists). The SDK internally uses `httpx2`, which coexists with our own `httpx`
- `httpx` — async HTTP client to the DevOps API
- `python-dotenv` + dataclass — config loading from `.env`
- `pydantic` — request models

## Common Commands

```bash
# Install / sync dependencies
uv sync

# Run the MCP server (streamable-http at http://127.0.0.1:8000/mcp)
uv run python main.py
```

There is currently **no test suite, linter, or formatter configured**. Do not invent test commands. Verification substitutes:

```bash
# Syntax-check the whole server
uv run python -m py_compile main.py server.py permissions.py config.py devops_client.py tools/workitems.py tools/attachments.py tools/testcases.py

# Confirm tool registration (12 tools currently)
uv run python -c "import asyncio, tools, main; print(len(asyncio.run(main.mcp.list_tools())))"
```

## Architecture

Layered structure with strict separation between MCP layer and HTTP layer:

```
main.py               → entry point: logging config, imports the tools package, mcp.run()
server.py             → shared MCPServer instance (v2), module-level credential-keyed
                       ClientRegistry (LRU) + get_client(ctx) facade reading ctx.headers;
                       registers the permission middleware (one mcp.middleware.append line)
permissions.py        → TOOL_PERMISSIONS mapping (13 tools) + permission_middleware:
                       fail-closed access control, unmapped tools allowed
tools/
  __init__.py         → imports domain modules to trigger @mcp.tool registration
  workitems.py        → work-item tools (list/create/details/comment/status-change)
  attachments.py      → attachment tools (preview/chunk/resource/image) + preview helpers
  testcases.py        → test-case tools (group & case create/query) + Step model helpers
devops_client.py      → DevOps HTTP API wrapper; persistent httpx.AsyncClient + user info
                       + permission cache (get_permissions, double-checked lock) +
                       STATUS_TRANSITIONS state machine for status-change validation
config.py             → Config dataclass; loads server-side DEVOPS_BASE_URL from .env /
                       process env (required; missing → ValueError at import, process
                       refuses to start — fail-fast)
```

**Key flow:** tool functions live in `tools/*.py` and register onto the shared `mcp` instance (from `server.py`) at import time; `main.py` merely imports the `tools` package and starts the server. Every `tools/call` first passes through `permissions.py::permission_middleware`: tools listed in `TOOL_PERMISSIONS` require the matching permission code fetched from `GET /api/devops/uc/permissions/employees?empId=&projectId=` (cached per `DevOpsClient`; `empId` is the **employee** id from current-user's `data["employee"]["empId"]`, not the top-level account `id` — the API rejects the latter with `EMPLOYEE_NOT_EXISTED`). Missing `X-DevOps-Project-ID`, an unreachable backend, or a failing permissions API all deny the call (fail-closed); unmapped tools pass through. Denials short-circuit with a `CallToolResult` marked **`is_error=True`** whose text is `{"error": ..., "required_permission": ...}` JSON. Each tool function is a thin adapter that:
1. Calls `get_client(ctx)` to obtain a `DevOpsClient` from the registry.
2. Calls the corresponding `DevOpsClient` method.
3. Reshapes the raw API JSON into a smaller dict (renaming/filtering fields).
4. Catches all exceptions and returns `{"error": "..."}` rather than raising — do not change this contract without reason, MCP clients depend on tool calls not throwing. Note the asymmetry: tool-layer errors are normal (non-isError) results, only middleware denials set isError.

**Status-change validation:** `change_workitem_status` validates before writing — `devops_client.validate_status_transition` fetches the item's real current status and checks `STATUS_TRANSITIONS[type][current_status]`; illegal targets are rejected with `{"error", "workitem_type", "current_status", "allowed_statuses"}` (no PUT sent). Status vocabularies differ per type — the same Chinese status maps to different English values (处理中 = `in-progress` for bug/task/risk but `developing` for story). The tables live in two places that must stay in sync: `STATUS_TRANSITIONS` (devops_client.py) and the `description=` text of `get_workitem_list` / `change_workitem_status` (tools/workitems.py).

**Work-item type mapping** appears in two places and they are **not identical** — keep them in sync when changing types:
- `devops_client.py` `workitem_type_map` — keyed by human-friendly names (`story`/`task`/`bug`/`risk`), used for **creating** items. Maps to `{workitemTypeId, workitemTypeName}`.
- `tools/workitems.py` `workitem_type` — keyed by numeric DevOps type ID (`2`/`3`/`4`/`5`), used for **reading/decoding** queried items.

IDs: `2`=故事/user-story, `3`=任务/task, `4`=bug, `5`=风险/risk.

**Priority conversion:** `devops_client.priority_convert` maps `P0`–`P4` to `highest`/`high`/`medium`/`low`/`lowest` (unknown values fall back to `"1"`). Work items only — test cases keep `P0`–`P4` verbatim.

**Time estimates:** `man_hour_convert` multiplies input hours by 3600 (DevOps API expects seconds); the read side (`format_time_estimate` in tools/workitems.py) formats seconds back to readable `1d2h` form (1d = 8h).

**Rich-text fields:** `devops_client.to_rich_text` normalizes HTML-ish fields (work-item `description`, test-case `note`/`precondition`): empty → `""`, anything that starts and ends with angle brackets passes through untouched, plain text gets wrapped in `<p>`.

**Authentication:** the MCP client injects credentials via HTTP headers (`X-DevOps-afcToken`, `X-DevOps-Project-ID` required; `X-DevOps-{Iteration,Module,Version}-ID` optional). The backend address is **server-side**: `DEVOPS_BASE_URL` env var (loaded by `config.py::Config.from_env` — `.env` for local dev, process env for deployment; one instance serves one backend). A client-sent legacy `X-DevOps-Base-URL` differing from the server config is rejected by `ClientRegistry.get` (prevents stale client configs silently writing to the wrong backend); matching or absent values pass. v2's lifespan is global (entered once at startup), so per-session clients were replaced by `ClientRegistry` in `server.py`, which separates transport from state: **one shared `httpx.AsyncClient` pool and one global `Semaphore`** serve all users (auth is per-request headers, so connections are credential-agnostic — backend concurrency stays ≤ `MAX_CONCURRENT_REQUESTS` no matter how many users), while the 5-tuple of credential header values keys a lightweight state cache (`UserInfo` + permission codes, a few KB each; LRU maxsize 256, eviction just drops the entry). `verify_token()` (`GET /api/devops/uc/users/current-user`) caches a `UserInfo` that client methods read via `self._user_info`. Header lookups lowercase the incoming mapping first, so plain dicts with mixed-case keys also work. A global shutdown lifespan closes the shared pool.

## Adding a New MCP Tool

1. Add the underlying HTTP method to `DevOpsClient` in `devops_client.py`. Follow the existing pattern: build the URL from `self.base_url`, call `self.get/post/put`, and `return r.json()` (or the parsed shape). Methods may read `self._user_info` — it is populated by `verify_token()` during session construction.
2. Add an `@mcp.tool(structured_output=False, ...)` adapter in the matching domain module under `tools/` (for a new domain, create the module and import it in `tools/__init__.py`). Use a concise `description=` (this is what the LLM sees). Wrap the body in try/except and return `{"error": ...}` on failure.
3. When reshaping API responses, only expose the fields a client needs — existing tools deliberately drop most raw fields. Also add the tool → permission-code entry to `TOOL_PERMISSIONS` in `permissions.py` (unmapped tools are allowed by default; map new tools deliberately).

## Configuration & Environment

Server-side configuration (`config.py::Config.from_env`, loads `.env` via python-dotenv; existing process env vars take precedence):

| Variable | Purpose | Required |
|---|---|---|
| `DEVOPS_BASE_URL` | DevOps instance root (e.g. `http://localhost:14080`); one instance serves one backend — whitespace and trailing `/` are normalized | yes |

Everything else is per-request via MCP client HTTP headers (`X-DevOps-afcToken`, `X-DevOps-Project-ID` required; `X-DevOps-{Iteration,Module,Version}-ID` optional). The historical `DEVOPS_USERNAME`/`DEVOPS_PASSWORD`/4-ID-vars mechanism is removed — the `.env` may still contain stale copies (`.env` holds real credentials and is **not** gitignored; verify before committing; see `.env.example`). A missing `DEVOPS_BASE_URL` raises `ValueError` at import → the process refuses to start (fail-fast).

## Transport

Server runs with `transport="streamable-http"` at `127.0.0.1:8000`, path `/mcp` (see `main.py::main`; v2 moved host/port/path into `run()` — the path parameter is `streamable_http_path`, not v1's `mcp_path`). There is no CLI/stdio transport configured. v2 serves both 2025-era (handshake) and 2026-07-28 (stateless) client generations; request bodies are capped at 4 MiB (the attachment chunk tool already paginates). OpenTelemetry middleware is enabled by default.

## Known Gotchas

- **Never return a bare list from a tool.** The SDK's `_convert_to_content` flattens lists into one TextContent block per element, and some clients/models only read the first block (this silently dropped all but the first item in practice). Wrap collections in a dict: `get_workitem_list` → `{"total","offset","limit","items"}`, `get_testcase_groups` → `{"total","groups"}`, `get_testcase_list` → `{"total","cases"}`. On failure these return `{"error": ...}` — never reintroduce `[{"error": ...}]`.
- All tools pass `structured_output=False` to `@mcp.tool(...)`: no output schema is advertised, results (success/error) are plain JSON text — otherwise strict clients reject error-shaped results with `-32600` ("has an output schema but did not return structured content"). Exception: `get_attachment_image` returns the SDK's `Image` helper on success (annotation `Image | dict`) — `_convert_to_content` turns it into a single `ImageContent` block; its error branches still return `{"error": ...}` JSON text.
- The SDK `Image` helper builds MIME as `image/{format}` **verbatim**, so `format="jpg"` yields the non-standard `image/jpg`. `MIME_MAP` in tools/attachments.py holds the correct mappings (`jpg` → `image/jpeg`); a previous implementation hand-constructed `mcp_types.ImageContent` specifically to control the MIME exactly.
- `get_attachment_resource` is currently disabled (commented out in tools/attachments.py); its `TOOL_PERMISSIONS` entry is retained as dead config for re-enabling. Tool count is 12 registered vs 13 mapped.
- Permission denials return `{"error": ..., "required_permission": ...}` as JSON text with `is_error=True` (`permissions.py::_deny`). The module/method docstrings in permissions.py still describe the older non-isError behavior — trust the code.
- The v2 low-level `ServerMiddleware` API is officially **provisional** — it may change within 2.x. All touchpoints are confined to `permissions.py` plus one `mcp.middleware.append(...)` line in `server.py`; the dependency is pinned `<3`.
- `X-DevOps-Project-ID` is a required header (the permissions API needs `projectId`); requests without it are rejected before any tool runs.
- Header lookups lowercase the mapping first (HTTP headers arrive lowercase via Starlette); registry keys therefore match regardless of the incoming casing.
- All 13 tools pass `structured_output=False` to `@mcp.tool(...)`: no output schema is advertised, results (success/error/denial) are plain JSON text — otherwise strict clients reject error-shaped results with `-32600` ("has an output schema but did not return structured content"). Exception: `get_attachment_image` returns `[TextContent(metadata JSON), ImageContent(base64)]` on success (SDK's `_convert_to_content` passes ContentBlock lists through unchanged); its error branches still follow the `{"error": ...}` JSON-text contract.
- List-returning tools serialize as **one TextContent block per element** (SDK's `_convert_to_content` flattens lists — same as v1), not a single JSON-array block.
- `client.get_project()` exists in `devops_client.py` but no MCP tool exposes it.
- Tool registration happens at import time: a new `tools/` domain module must be imported in `tools/__init__.py`, otherwise its tools never register.
- A client-sent legacy `X-DevOps-Base-URL` is consistency-checked in `ClientRegistry.get`: mismatch with server `DEVOPS_BASE_URL` → `ValueError` surfaced as the standard denial/error JSON (prevents stale client configs silently hitting the wrong backend); matching or absent passes.
