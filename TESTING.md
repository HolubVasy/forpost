# ForPost - Testing Plan

Tests are written with pytest. Every phase in PLAN.md must have its tests
passing before moving to the next phase. No phase is "done" until its
tests are green.

## General Approach

- Unit tests for each module in isolation (auth, modes, executor, logging)
- Integration tests hitting the FastAPI endpoint with TestClient
- No tests require a real VPS or real docker socket in CI - executor tests
  use simple, safe commands (e.g. `echo`, `ls`) that work anywhere
- Destructive or environment-specific commands (docker, gh, git against
  real remotes) are tested manually after deployment (Phase 4/5), not in
  the automated suite

## Phase 1: Core Service Tests

### test_auth.py
- Request without `X-API-Key` header -> 401
- Request with wrong key -> 401
- Request with correct key -> passes through to next check

### test_modes.py
- **read-only**: explicit list of read commands, each must be allowed:
  `docker ps`, `docker logs <name>`, `ls`, `cat <file>`, `git status`,
  `git log`, `df -h`, `ps aux`
- **read-only**: explicit list of write/destructive commands, each must
  be rejected: `rm`, `docker restart`, `docker rm`, `git push`, `git pull`,
  `docker compose up`, `> file` (redirect/write)
- **whitelist**: command matching an entry in `whitelist.config` -> allowed
- **whitelist**: command not matching any entry -> rejected
- **whitelist**: `whitelist.config` missing or empty -> fail closed,
  nothing is allowed (not "allow everything")
- **full-access**: any command -> allowed (including ones that would be
  rejected in the other two modes)
- Invalid/unknown mode value -> rejected with clear error

Note: read-only validation should be based on an explicit list of
recognized read command prefixes, not a denylist of "write-ish" patterns -
denylist heuristics produce false positives/negatives (e.g. `git log`
contains no write keywords but a naive pattern match could still
misclassify it). Maintain the allowed-read list as an explicit, testable
set.

### test_executor.py
- Simple command (`echo hello`) -> stdout contains "hello", exit_code 0
- Failing command (`exit 1` or `false`) -> exit_code 1, no exception raised
- Command producing stderr -> stderr captured separately from stdout
- Long-running command behavior is documented but not load-tested here
  (no hard timeout implemented in Phase 1 per simplified spec - if added
  later, add a test for timeout behavior)

### test_logging.py
- After a command runs, `commands.jsonl` has a new line with correct
  fields: timestamp, mode, command, exit_code, output
- Service startup writes an entry to `app.log`
- An auth failure writes an entry to `app.log` (not to `commands.jsonl`,
  since no command was evaluated)

### Integration (test_main.py)
- POST with valid key, read-only mode, safe read command -> 200, correct
  output, command logged
- POST with valid key, read-only mode, write command -> rejected (4xx),
  reason indicates mode violation, NOT logged as executed (or logged with
  exit_code/status indicating rejection - decide and document)
- POST without key -> 401, no command executed, no entry in commands.jsonl

## Phase 2: MCP Wrapper Tests

- MCP tool schema for `execute_command` validates (correct param names/types:
  command, mode)
- Calling the MCP tool with valid params produces the same result as the
  equivalent HTTP request
- Calling with missing required param -> clear error, no execution

## Phase 3: Containerization Tests (manual + smoke)

- `docker compose up` succeeds without errors
- Container runs as non-root `forpost` user (`docker exec ... whoami`)
- `forpost` user can run `docker ps` (docker group membership works)
- From host: `curl` to container's exposed port with valid key returns 200

## Phase 4: Deployment Tests (manual + smoke)

- HTTPS endpoint reachable from outside VPS
- Valid API key -> 200
- Invalid/missing API key -> 401
- Certificate valid (no browser warning)

## Phase 5: Integration Tests (manual, via Claude chat)

- Connector: added in Claude and reachable; **signs in via Google (OIDC)** with
  an allow-listed email — verified live. ✅
- Read-only: `docker ps`, `docker logs <container>` return real output ✅
- Whitelist: a whitelisted command succeeds (verified with `gh pr list` on a
  private work repo); a command NOT in `whitelist.config` is rejected ✅
- Full-access: an arbitrary one-off command executes and returns output
  (verified with `gh repo create` + delete in the personal account) ✅
- `diagnostics` from chat returns a live snapshot (real memory/uptime/versions) ✅
- End to end: BriefDay v0 deployed via ForPost (gh repo interactions,
  file writes, docker compose up, logs read back) — future first real project.

## Phase 5: Hardening & Extension Tests (automated)

Added alongside the original suite as the surface grew (whole suite ~178
passing, 1 skipped):

### test_mcp_http.py — the mounted `/mcp` HTTP guard
- `POST /mcp/` without a key -> 401; wrong key -> 401; 401 body is valid JSON
- `initialize` with the correct `X-API-Key` -> 200, response mentions `ForPost`
- `/mcp` is rate-limited (429 after the configured per-minute limit)

### test_oauth.py — optional Google-OIDC bearer path
- Allow-list: exact-email match and `@domain` rule (allowed vs rejected)
- `oauth_enabled()` toggles on `OAUTH_ENABLED`; `verify_bearer` returns `None`
  (no network call) when disabled
- Guard `_authenticated`: accepts a valid `X-API-Key`; accepts a valid bearer
  only when OAuth is on; rejects a bad bearer; rejects when neither is present

### test_service.py — diagnostics & reset
- `diagnostics()` returns the expected shape (`ok`, checks, memory, jobs,
  recent, versions) without a real docker daemon
- `reset(scope=...)` clears the right things per scope (`jobs` / `logs` /
  `workspace` / `all`); invalid scope -> error

### test_jobs.py — async job lifecycle
- `run_async` starts a job and returns an id; `job_status` follows growing
  output and paginates via `next_offset`; `job_kill` stops a running job;
  finished jobs are reaped after retention; per-job log cap is enforced

### Command safety / limits (in test_modes.py / test_executor.py)
- Shell metacharacters (`; | & \` $ ( ) < >`, newlines) rejected in
  read-only/whitelist; token-boundary matching (`ps` != `psql`, `ls` != `lsof`)
- Output over `MAX_OUTPUT_CHARS` is truncated (tail kept); a command timeout
  kills the process and returns partial output with `timed_out=true`

## Regression Checklist (run before any future change is considered done)

- [ ] All Phase 1 automated tests pass
- [ ] All Phase 2 automated tests pass
- [ ] Phase 3 smoke checks pass (container builds and runs)
- [ ] If touching auth/modes: re-run test_auth.py and test_modes.py fully
- [ ] If touching logging: verify both commands.jsonl and app.log still
      get separate, correctly-formatted entries
