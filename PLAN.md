# ForPost - Project Plan

This is the working plan, broken into phases. Each phase ends with tests
passing before moving to the next. See `TESTING.md` for the testing
strategy.

## Phase 0: Repo Setup (done)

- [x] Create private GitHub repo `forpost`
- [x] Add SPEC.md, CLAUDE.md, README.md, .env.example, .gitignore

## Phase 1: Core Service

Goal: a FastAPI service that accepts a command + mode, checks auth,
applies mode logic, executes, logs, and returns the result.

Build and test incrementally - do not write all modules then test at
the end. Each step below includes its own test pass before moving on.

- [x] 1a. Scaffold `app/` package structure, `requirements.txt`
      (fastapi, uvicorn, pydantic, python-dotenv, pytest)
- [x] 1b. `app/auth.py` - API key dependency, 401 on missing/invalid
      -> write + run `test_auth.py`, must pass before continuing
- [x] 1c. `whitelist.config` + `app/modes.py` - read-only / whitelist /
      full-access decision logic
      -> write + run `test_modes.py`, must pass before continuing
- [x] 1d. `app/executor.py` - subprocess execution, capture
      stdout/stderr/exit_code
      -> write + run `test_executor.py`, must pass before continuing
- [x] 1e. `app/logging_config.py` - writers for `logs/commands.jsonl`
      and `logs/app.log`
      -> write + run `test_logging.py`, must pass before continuing
- [x] 1f. `app/main.py` - wire auth -> modes -> executor -> logging
      together, single POST endpoint
      -> write + run `test_main.py` (integration), must pass
- [x] 1g. (SPEC additions) `app/file_ops.py` + write/read/delete_file
      endpoints, `app/rate_limit.py` (429), 5-min command timeout
      -> `test_file_ops.py`, `test_rate_limit.py` pass

**Exit criteria:** `uvicorn app.main:app --reload` runs locally. `curl
-X POST` with valid key + read-only mode + a safe command (e.g. `echo
hello`) returns output and logs it. Invalid key returns 401. A write
command in read-only mode is rejected. All Phase 1 tests green.

## Phase 2: MCP Wrapper

Goal: expose the same functionality as an MCP tool Claude can call.

- [x] `app/mcp_server.py` - FastMCP wrapper exposing `execute_command(command, mode)`
      plus file-op tools `write_file_tool` / `read_file_tool` / `delete_file_tool`.
      Reuses the Phase-1 logic (validate_command, execute, file_ops, logging,
      rate_limiter) - no duplicated rules.
- [x] Verify MCP tool schema is valid (local test/inspection)
- [x] Tests for Phase 2 (`test_mcp_server.py` - 14 tests)

**Exit criteria:** MCP tool schema loads without errors; tool call maps
to the same logic as the HTTP endpoint. ✅ met (97 tests green).

Run the MCP server: `python -m app.mcp_server` (stdio by default;
set `MCP_TRANSPORT=streamable-http` for remote/VPS use).

## Phase 3: Containerization

Goal: ForPost runs in Docker, ready to deploy.

- [ ] `Dockerfile` - Ubuntu base, install bash/git/gh/docker-cli/docker-compose
- [ ] `docker-compose.yml` - service definition, volume for `/home/forpost/`,
      mount docker socket, env vars from `.env`
- [ ] Non-root `forpost` user in container, member of `docker` group
- [ ] Build and run locally, smoke test with `docker compose up`

**Exit criteria:** container builds, starts, responds to a read-only
request from the host machine.

## Phase 4: Deployment

Goal: ForPost running on VPS, reachable via HTTPS.

- [ ] Create dedicated GitHub bot account + token, add to `.env` on VPS
- [ ] Generate `API_KEY` via `openssl rand -hex 32`, add to `.env` on VPS
- [ ] Clone repo to VPS, `docker compose up -d --build`
- [ ] Configure domain + Let's Encrypt HTTPS (Caddy or nginx reverse proxy)
- [ ] Smoke test from outside: read-only `docker ps` over HTTPS

**Exit criteria:** `https://forpost.<domain>` responds correctly with
valid API key, rejects without.

## Phase 5: Integration

Goal: ForPost usable from Claude chat, end to end.

- [ ] Connect ForPost as MCP connector in Claude
- [ ] Test read-only mode (docker ps, logs)
- [ ] Test whitelist mode (git pull, docker restart on a test container)
- [ ] Test full-access mode (one-off command)
- [ ] First real task: deploy BriefDay v0 through ForPost

**Exit criteria:** all three modes work end to end from Claude chat;
BriefDay v0 successfully deployed via ForPost.

## Out of Scope / Deferred

- Multi-VPS support
- Web dashboard
- Log rotation (logrotate) - add only if logs become unwieldy
- Any of the items listed in SPEC.md "Out of Scope"
