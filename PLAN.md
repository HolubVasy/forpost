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

## Phase 3: Containerization ✅ (done - 2026-07-26)

Goal: ForPost runs in Docker, ready to deploy.

- [x] `Dockerfile` - Ubuntu 24.04 base, bash/git/gh/docker-cli/docker-compose
      + Python venv; `GET /health` endpoint added for the container HEALTHCHECK
- [x] `docker-compose.yml` - service definition, `forpost_home` volume for
      `/home/forpost/`, docker socket mount, env from `.env`, `DOCKER_GID`
      build arg to match the host docker group; `.dockerignore` added
- [x] Non-root `forpost` user in container, member of `docker` group
      (verified: `uid=1001(forpost) ... groups=...,999(docker)`)
- [x] Build and run locally, smoke test — image builds; container starts and
      is HEALTHCHECK-healthy; `/health` 200, read-only `echo` 200, no key 401,
      write in read-only 403. `docker compose config` validates.
      (Local smoke used `docker run` since the unix socket can't be mounted on
      Docker Desktop/Windows; the socket mount is exercised on the Linux VPS.)

**Exit criteria:** container builds, starts, responds to a read-only
request from the host machine.

## Phase 4: Deployment ✅ (done - 2026-07-26)

Goal: ForPost running on VPS, reachable via HTTPS.

Deployed to the German Zomro VPS (`188.137.227.248`) at
`https://forpost.poly-mind.net`, alongside the existing LibreChat/jobs stack.

- [ ] Dedicated GitHub bot account + token — deferred; `GITHUB_TOKEN` left
      empty in `.env` for now (only needed when `gh` is used inside the tunnel)
- [x] Generate `API_KEY` via `openssl rand -hex 32`, stored in
      `/opt/forpost/.env` (chmod 600) on the VPS
- [x] Deploy to VPS: code in `/opt/forpost`, VPS-specific `docker-compose.yml`
      (joins the external `librechat_default` network so the existing
      `LibreChat-NGINX` proxy can reach it as `http://forpost:8000`;
      `DOCKER_GID=999` matches the host docker group), `docker compose up -d`
- [x] Domain + HTTPS: A record `forpost.poly-mind.net` -> VPS (Namecheap);
      Let's Encrypt cert via **certbot standalone** (brief nginx stop, same
      method as chat/jobs); added `server` blocks to the shared
      `/opt/LibreChat/client/nginx.conf` (backup taken, `nginx -t`, reload)
- [x] Smoke test from outside: `https://forpost.poly-mind.net/health` 200,
      read-only `docker ps` with API key returns the container list, no key ->
      401. Existing chat/jobs untouched.

**Exit criteria:** `https://forpost.<domain>` responds correctly with
valid API key, rejects without. ✅ met.

**⚠️ Cert auto-renew caveat:** the cert is standalone-authenticated, which
needs port 80 free, but `LibreChat-NGINX` holds it — so the scheduled
`certbot renew` will fail (same as the existing `chat` cert). Renew manually
before 2026-10-24 (and add to any renewal cron):
`docker stop LibreChat-NGINX && certbot renew && docker start LibreChat-NGINX`.

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
