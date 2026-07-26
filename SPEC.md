# ForPost - Specification v1.1 (Final)

## Current State (authoritative, 2026-07-26)

This document also records the **decision history** (dated sections below), where
some early choices were deliberately superseded. For the current, authoritative
description use `README.md` and `CLAUDE.md`. In short, as built and deployed:

- **Tools (9):** `execute_command` (sync) + `run_async`/`job_status`/`job_kill`
  (async jobs for long commands) + `write_file`/`read_file`/`delete_file` +
  `diagnostics` (one-call health snapshot) + `reset` (self-cleanup by scope).
  All exposed as both MCP tools and REST endpoints over one shared service layer.
  (Supersedes the early "one tool, no write_file, no polling" notes.)
- **Modes:** read-only / whitelist / full-access; read-only & whitelist also
  reject shell metacharacters **and run as an unprivileged user** (privilege
  separation - see decision below). read-only allows local inspection (ls, cat,
  git status/log, df, ps...); `whitelist.config` allows git pull/fetch/status/
  log + read-only `gh` (pr list/view/diff/status/checks, repo view, issue
  list/view). Docker commands need the socket, so they are full-access only.
- **Timeout:** per-call `timeout` (default 300s, max `MAX_TIMEOUT_SECONDS`),
  not a hardcoded 5 min.
- **Output:** streamed, memory-bounded, capped (`MAX_OUTPUT_CHARS`).
- **Logs:** two streams (`commands.jsonl` audit + `app.log` operational),
  size-budgeted (`MAX_LOG_BYTES`: trim-oldest for the audit log, rotate for
  app.log) with secrets redacted in the audit trail.
- **Transport:** FastAPI REST + FastMCP streamable-HTTP at `/mcp`, behind nginx
  (HTTPS, `limit_req`, `proxy_read_timeout` 1800s). Auth: static `X-API-Key`
  (constant-time) **or** an optional Google-OIDC bearer on `/mcp` (verified
  audience + allow-listed email), plus app + nginx rate limits.
- **MCP self-description:** server `instructions`, per-tool annotations, and a
  `forpost://manual` resource.
- **Deployed:** `https://forpost.poly-mind.net` (MCP: `.../mcp/`). Phases 1–5
  done; the Claude connector is registered and working via Google OIDC.

## Project Goal

Give Claude a simple, secure tunnel into the VPS so infrastructure tasks
(repo creation, code deploy, container management, log inspection) can be
done from chat on any device, without SSH and without copy-pasting commands
manually. Keep it as simple as possible: one endpoint, one tool, three
modes, API key, logs. No smart logic on ForPost's side - all reasoning
happens on Claude's side.

## Core Idea

Claude never edits files directly on the server. Source of truth is always
GitHub. The flow:

```
Idea -> Spec -> Claude writes code -> push to GitHub -> VPS pulls and runs
```

This rule exists because of a past incident: Claude once edited a file
directly on the server, the branch diverged from GitHub, and it caused
confusion about why pushes weren't reflected. Never again.

## What ForPost Is

A tunnel. API key + command + mode in, result out, logged. Nothing else.

## Stack

- Docker container, Ubuntu-based (matches VPS host OS)
- Non-root user `forpost`, member of `docker` group, scoped to `/home/forpost/`
- Base tools: bash, git, gh (dedicated GitHub bot account/token, not personal),
  docker + docker compose
- Additional tools (Python, Node, .NET, etc.) installed on-demand as projects require
- Domain with HTTPS (Let's Encrypt) - e.g. `forpost.<domain>`
- Reusable task aliases live as shell scripts in `/home/forpost/bin/`,
  callable via execute_command; library grows organically

## Core Contract

One tool: `execute_command`

- Input: command (text), mode (read-only / whitelist / full-access)
- Output: stdout, stderr, exit_code
- No `write_file` - files written via heredoc inside execute_command
- No retries, no auto-fix, no rollback - errors returned as-is for
  Claude/user to interpret

## Modes (declared per request)

1. **Read-only** - only read/inspection commands allowed (logs, status,
   ls, cat, docker ps). Any write/change command in the request is
   rejected outright.

2. **Whitelist** - command checked against a list in ForPost's own config
   file on VPS (git ops, docker restart/logs, deploy aliases, etc.). The
   request just declares "whitelist mode"; ForPost validates against its
   own list.

3. **Full Access** - anything goes, one request, immediate execution, no
   confirmation step. Fully logged regardless.

## Auth

- Single static API key, checked before anything else. Missing/invalid -> 401.
  Compared in constant time (`hmac.compare_digest`) to avoid a timing side-channel.
- Rotation: **at least once a year** as routine hygiene, and immediately on any
  suspected leak. (This is key rotation - distinct from log rotation, which is
  automatic and file-based.)
- **Optional Google-OIDC on `/mcp`** (added 2026-07-26, see decision section
  below): when `OAUTH_ENABLED`, the `/mcp` guard *also* accepts a Google bearer
  token — a request is authorised if it satisfies *either* the key *or* a valid
  bearer. This exists because the Claude connector dialog offered no header-auth
  field; the static key remains the whole boundary for the REST surface.

## Logging

Two separate log streams:

- **Command logs** (`logs/commands.jsonl`) - every execute_command call:
  timestamp, mode, command, exit_code, output. Audit trail of what was run.
- **Application logs** (`logs/app.log`) - ForPost's own runtime logs
  (startup, errors, auth failures, crashes). Operational/debugging data,
  kept separate from the command audit trail.

No database. JSONL/text files. **Rotation is configured in-app** (revised
2026-07-26, see decision section): both files share a size budget
(`MAX_LOG_BYTES`); when `commands.jsonl` exceeds it the oldest
`LOG_TRIM_FRACTION` of lines are dropped, and `app.log` rotates (one `.1`
backup). Secrets (`ghp_`/`github_pat_`/`sk-`/`AKIA…`/`Bearer`/`X-API-Key`) are
**redacted** in the command audit log.

## Repo & Deploy Workflow (typical usage, not separate tools)

1. `gh repo create` (private by default, public if explicitly requested)
   using dedicated ForPost GitHub account
2. Write code files via heredoc
3. Add `.github/workflows/*.yml` if CI needed
4. Push
5. `git pull && docker compose up -d --build` on VPS (deploy alias)
6. `docker logs <name>` for inspection
7. Restart/redeploy as needed

## Permissions

**No root.** ForPost runs as a regular user `forpost`, added to the `docker`
group (gives docker access without full root), with permissions scoped to
`/home/forpost/`. This covers the full target workflow:

- `gh repo create` - needs only a GitHub token, not root
- Writing code files - normal user permissions in own directory
- GitHub Actions - just a `.github/workflows/*.yml` file pushed to the repo,
  runs on GitHub's infrastructure, not VPS
- Deploy - `git pull` + `docker compose up`, via docker group membership
- Logs/redeploy/restart - `docker logs`, `docker compose down/up`,
  `docker restart`, all via docker group

## On Direct SSH Access

Claude does not have a tool for direct SSH/terminal access to any server -
this is a hard constraint of the environment, not a policy choice. ForPost
exists specifically to bridge this gap: it's an HTTP/MCP service that Claude
*can* call, which then executes commands on Claude's behalf inside its
scoped user account.

## Out of Scope (deliberately)

- No Azure/AWS-style orchestration or dashboard
- No multi-user accounts or roles (the optional Google-OIDC layer added
  2026-07-26 is just an email allow-list gating a single-user tool, not a
  multi-tenant auth system), no token-rotation system
- No automated rollback/snapshot system
- No IP allowlisting (API key / OIDC is sufficient)
- No structured DB for logs (Postgres, Mongo, vector DB all considered
  and rejected)

## Project Tasks (what ForPost must let Claude do day to day)

1. Create a new GitHub repo (private by default) with starter files,
   from a phone, mid-conversation.
2. Write code into that repo (multiple files, including config/yaml/Dockerfile).
3. Push code, optionally with a GitHub Actions workflow.
4. Deploy the repo to VPS as a Docker container (git pull + compose up).
5. Read logs of any running container, including containers not created
   via ForPost.
6. Restart/redeploy an existing container after a code change.
7. Inspect general VPS state (docker ps, disk space, running processes)
   for troubleshooting.
8. Occasionally run a one-off command that doesn't fit any category above
   (debugging, cleanup, exploration).

## File Operations (Reconsidered, 2026-06-15)

Earlier decision was "no write_file, heredoc covers it." This holds for
small text files (Python, YAML, .env, configs). It breaks down for:
- Binary files (PDFs, images) - heredoc + base64 is workable but painful
- Large files - long single command strings are fragile (escaping, length limits)

**Revised approach:** Add explicit file operations alongside execute_command:

- `write_file(path, content, encoding)` - encoding is "text" or "base64".
  Text for code/config, base64 for binaries (PDFs, etc.)
- `read_file(path, encoding)` - mirror of write_file, for reading back
  binaries or large files without dumping them through stdout
- `delete_file(path)` - explicit removal, logged like any other operation

These are still simple primitives, not smart logic - they read/write
bytes to a path within `/home/forpost/`, subject to the same mode rules
as execute_command (read-only = read_file only, whitelist/full-access
allow writes per mode rules).

**Still no change to the core principle**: ForPost executes and returns,
no interpretation. File ops are just a more efficient transport for
content than cramming everything through command strings.

**Logging**: file operations get their own line in commands.jsonl too
(operation type "write_file"/"read_file"/"delete_file", path, size,
mode) - same audit trail, different operation type.

**Status**: this extends Phase 1 scope slightly. If Phase 1 is already
complete or far along, treat this as Phase 1.5 / early Phase 2 addition
rather than reopening finished work.

**Editing existing files (common workflow)**: "fix this script, it's
failing" = `read_file(path)` -> Claude edits content -> `write_file(path,
new_content)`. No separate `update_file` tool needed - read+write covers
edits. This is the same pattern as local file editing, just over the
ForPost transport.

## Rate Limiting (2026-06-15)

No retry logic - if a request fails, it returns an error as-is (per
"no auto-fix" principle). However, a simple rate limit protects against
runaway request loops (a bug in the caller sending requests too fast,
not malicious traffic).

- Configurable via `.env`, e.g. `RATE_LIMIT_PER_MINUTE=120`
- Default: 120 requests per minute (raised from 60 on 2026-07-26); it covers
  *actions* only — queries (`read_file`, `job_status`, `job_kill`) are exempt so
  polling/killing a job is never throttled
- Exceeding the limit returns 429, request not executed, logged to app.log
- This is "protection from self" (same framing as the whitelist) - a
  safety net against accidental loops, not a security feature against
  external abuse (API key is the security boundary)

Against actual external floods / key brute-force there is a second layer at
the reverse proxy (2026-07-26): nginx `limit_req` (20 r/s, burst 40) +
`limit_conn` (30) on the forpost vhost, returning 429. This covers all paths
(incl. pre-auth), which the app-level limiter alone does not.

## Response Format (2026-06-15, clarification)

Response is a simple JSON object with plaintext fields - no nested
schemas, no special encoding beyond what's needed for file operations
(base64 for binary content in read_file/write_file, per File Operations
section above).

```json
{
  "stdout": "...",
  "stderr": "...",
  "exit_code": 0
}
```

For file operations, response includes the relevant field (e.g. content
for read_file, success/path for write_file). No further structure -
plaintext is the default, keep it simple.

## Command Timeout (2026-06-15, revised 2026-07-26)

Every command run via execute_command has a timeout. Original decision was a
hardcoded 5 minutes; **revised 2026-07-26** to a **per-call `timeout`
parameter** (default 300s) clamped to a server max `MAX_TIMEOUT_SECONDS`
(default 1800s). On timeout ForPost kills the process and returns whatever
stdout/stderr was captured so far, plus `timed_out=true`.

The command runs in a worker thread so a long command does not block the event
loop (and `/health` stays responsive during it).

Timing chain (deployed): the effective ceiling for a synchronous command is
`min(app timeout, nginx proxy_read_timeout, connector timeout)`. nginx's default
`proxy_read_timeout` is 60s, which would 504 a >1-minute command before the app
finished - so the forpost vhost raises `proxy_read_timeout`/`proxy_send_timeout`
to 1800s to match `MAX_TIMEOUT_SECONDS`. (Verified: a 65s command returns 200.)
For anything genuinely long-running, use the async job tools instead.

This stays synchronous - one request, one response. No request IDs, no
polling, no separate kill endpoint. If something hangs, the timeout
auto-kills it and Claude sees "timed out" in the response, then can
follow up (e.g. `docker ps` to check for leftover processes).

## Command Safety in read-only / whitelist (2026-07-26)

read-only and whitelist validate by allowed command prefix, but the command is
run through a shell - so a bare prefix check would let `ls; rm -rf` or
`git && curl|sh` smuggle a second command. Therefore **shell metacharacters
(`; | & \` $ ( ) < >` and newlines) are rejected** in read-only and whitelist
mode. This is "protection from self", not a sandbox: it does not neuter
dangerous flags of an otherwise-allowed command (e.g. `find -delete`).
full-access is the deliberate escape hatch for chaining/pipes.

## Output Truncation (2026-07-26)

Command stdout/stderr are streamed on background threads into a fixed-size ring
buffer that keeps only the last `MAX_OUTPUT_CHARS` bytes (default 100000). This
bounds **memory** during execution (a gigabyte-output command can't spike RAM),
and the same cap protects the response, the model's context, and the on-disk
`commands.jsonl` from runaway output. Dropped byte count is reported inline.

## Async Jobs (2026-07-26 - revises "no request IDs / no polling")

The synchronous `execute_command` is bounded by its timeout and returns only
when done. For tasks that outlive one request (`docker build`, deploys),
ForPost adds a background job model. This intentionally revises the earlier
"no request IDs, no polling, no separate kill endpoint" decision.

- `run_async(command, mode, timeout)` -> starts the process and returns a
  `job_id` immediately.
- `job_status(job_id, offset)` -> `{status, running, exit_code, output,
  offset, next_offset, total_bytes, truncated}`. Poll to follow output as it
  grows; `next_offset` both resumes the follow and paginates.
- `job_kill(job_id)` -> terminates a running job.

Implementation: the child writes stdout+stderr straight to a per-job log file
(`LOGS_DIR/jobs/<job_id>.log`) via its own fd, so job output never buffers in
ForPost's memory. A supervisor thread enforces the timeout and a per-job disk
cap (`MAX_JOB_LOG_BYTES`). Job state is in-memory (lost on restart; log files
persist). Still no streaming push - MCP can't stream partial output mid-call,
so the caller polls.

## MCP Self-Description (2026-07-26)

The MCP surface is self-documenting so any client can discover capabilities:
- `initialize` returns server `instructions` (a built-in usage manual).
- `tools/list` returns each tool's `title` + JSON Schema + `annotations`
  (`readOnlyHint` / `destructiveHint` / `idempotentHint` / `openWorldHint`).
- a `forpost://manual` resource (markdown) provides a fuller reference via
  `resources/list` + `resources/read`.

## Diagnostics & Reset (2026-07-26)

Two operational tools were added (both MCP + REST), so state can be inspected and
cleaned without shell spelunking:

- `diagnostics()` / `GET /diagnostics` — one fixed, safe set of probes (no mode,
  no arbitrary command): `ok` (alive & healthy), `uptime_seconds`, `checks`
  (docker reachable, workspace + logs writable), `memory` (process RSS + system
  available/total), `jobs` (running / by-status / zombie count), `recent`
  (failed-command summary + last app WARNING/ERROR), tool `versions`, and
  `commands_log_bytes`. Read this instead of opening the logs.
- `reset(scope)` / `POST /reset` — clears **ForPost's own** state only, never the
  host: `jobs` (kill running + wipe job state/logs), `logs` (truncate
  commands.jsonl + app.log and remove rotated backups), `workspace` (delete
  everything under the managed workspace), `all` (jobs + logs + workspace).

## Log Rotation & Redaction (2026-07-26 — revises "logrotate deferred")

The earlier "rotation via logrotate if/when relevant" is replaced by in-app
handling so a long-lived instance can't fill the disk or leak secrets:

- **Budget + trim:** each log file has a byte budget `MAX_LOG_BYTES` (default
  10 MB). When `commands.jsonl` exceeds it, the oldest `LOG_TRIM_FRACTION`
  (default 0.2) of lines are dropped; `app.log` uses a `RotatingFileHandler`
  keeping one `.1` backup.
- **Redaction:** before an audit line is written, secret-shaped tokens
  (`sk-…`, `ghp_…`, `github_pat_…`, `AKIA…`, `Bearer …`, `X-API-Key: …`) are
  masked, so a command that echoes a token doesn't persist it in cleartext.
- `reset(scope="logs"|"all")` truncates both streams on demand.

## Optional Google-OIDC on /mcp (2026-07-26)

ForPost's own auth is the static `X-API-Key`. But the Claude custom-connector
dialog (on the account used) exposed no request-header field — only an OAuth
flow. So `/mcp` gained an **optional** second credential, enabled with
`OAUTH_ENABLED`:

- ForPost acts as an OAuth **resource server** only — Google is the
  authorization server; ForPost never sees a password. A presented Google
  **bearer** token is verified against Google's `tokeninfo` endpoint.
- Accepted only if: `aud == OAUTH_CLIENT_ID` (blocks a token minted for another
  app — confused-deputy guard), `email_verified` is true, and the email is in
  `OAUTH_ALLOWED_EMAILS` (an exact address, or a `@domain` rule). Positive
  results are cached up to ~5 min (bounded by the token's own `exp`).
- Discovery follows RFC 9728: a `401` carries
  `WWW-Authenticate: Bearer resource_metadata="…/.well-known/oauth-protected-resource"`,
  and that endpoint advertises `https://accounts.google.com` as the auth server.
- A request to `/mcp` is authorised if it satisfies **either** the key **or** a
  valid bearer; the guard then applies the rate limit. Turned off, ForPost stays
  key-only. The Google OAuth client is a *Web application* client whose
  authorized redirect URI is `https://claude.ai/api/mcp/auth_callback`.

This is not multi-user auth — it's a single-user allow-list so that arbitrary
Google users can't reach a publicly-hosted `/mcp`.

## Privilege Separation (2026-07-26 - makes read-only a real boundary)

A review found that read-only was **not** a confidentiality boundary: `cat ` was
allowed, and `cat /proc/1/environ` dumped the app's environment - including the
`API_KEY`. Since a leaked key + full-access + the docker socket = root on the
host, read-only could self-escalate. Stripping the key from the *child's* env
did not help (the child reads PID 1's env, not its own).

Fix: **restricted-mode commands run as a separate, unprivileged user.**

- The container has two users: `forpost` (runs the app + full-access commands;
  in the docker group) and `forpost-ro` (runs read-only/whitelist commands; in
  the `forpost` group for the shared workspace, but NOT the docker group). The
  app launches restricted commands as `forpost-ro` via `sudo -n -H -u` (a
  NOPASSWD sudoers rule for that runas only). Config: `RESTRICTED_COMMAND_USER`
  (empty locally / on Windows = run in-process, no drop).
- Because `forpost-ro` is a different uid, `cat /proc/1/environ` and reading
  `forpost`-owned files return *permission denied* - `/proc/<pid>/environ` is
  0400 by uid, and the logs dir is 0700. So read-only cannot read the key,
  the audit log, or the docker socket. Docker inspection therefore moved to
  full-access; `docker ps`/`docker logs` left the read-only allow-list and the
  docker entries left `whitelist.config`.
- **Token scoping by mode** (matches "work repo = read, own repos = all"):
  read-only gets no git token; whitelist gets the **work** token
  (`GITHUB_WORK_TOKEN`) - and since whitelist allows only read commands, ForPost
  can never *write* to the work repo; full-access gets the **personal** token
  (`GITHUB_TOKEN`). The chosen token is exposed as `GITHUB_TOKEN`/`GH_TOKEN`;
  the other is never present.

Full-access still runs as `forpost` with the docker socket - the escape hatch,
root-equivalent by design. The guarantee is only about the restricted modes.

## MCP OAuth-only option (2026-07-26)

`MCP_REQUIRE_OAUTH=true` makes `/mcp` accept **only** a Google-OIDC bearer - the
static `X-API-Key` is refused there (it still guards REST). So a leaked key
cannot drive the MCP tunnel; only an allow-listed Google account can. Addresses
the review point that, while both credentials are accepted, the key is the
weakest link on `/mcp`.

## gh read-only in whitelist mode (2026-07-26)

`whitelist.config` gained read-only GitHub CLI prefixes — `gh pr
list/view/diff/status/checks`, `gh repo view`, `gh issue list/view` — so PRs and
repos can be inspected in whitelist mode. Write actions (`gh repo create`, `gh pr
merge`, …) remain full-access only. In practice two `gh` tokens are wired: a
personal one (repo creation, full) and a work one (private-PR read, `repo`
scope).

## Workspace Cleanup (2026-06-15)

`/home/forpost/workspace/` accumulates temporary files (code written via
write_file before pushing to GitHub). Beyond the per-task cleanup
(rm after push), a system-level cron job on the VPS independently removes
anything older than 1 day:

```
0 3 * * * find /home/forpost/workspace -mtime +1 -delete
```

This is plain VPS cron, not part of Forpost itself - works regardless of
whether Forpost is running, restarted, or crashed.

## Status

Spec complete (with File Operations addition above). Ready to build /
continue building.

## Build Plan

### Phase 1: Core Service
1. Scaffold project structure (Python/FastAPI)
2. Implement auth middleware (API key check, 401 on failure)
3. Implement `execute_command` endpoint: receive command + mode
4. Implement mode logic (read-only / whitelist / full-access)
5. Execute command via subprocess, capture stdout/stderr/exit_code
6. Write to `commands.jsonl` (command log)
7. Write to `app.log` (application log)
8. Return response

### Phase 2: MCP Wrapper
1. Wrap the FastAPI service in MCP protocol (tool: execute_command with
   command + mode params)
2. Test locally that MCP tool schema is valid

### Phase 3: Containerization
1. Dockerfile: Ubuntu base, install bash/git/gh/docker-cli/docker-compose
2. docker-compose.yml: ForPost service, mount `/home/forpost/` volume,
   env vars for API keys
3. Non-root `forpost` user inside container, in `docker` group
   (mount host docker socket)

### Phase 4: Deployment
1. Provision dedicated GitHub bot account + token
2. Deploy container to VPS
3. Configure domain + HTTPS via existing nginx reverse proxy (certbot)
4. Smoke test: read-only `docker ps` from outside

### Phase 5: Integration
1. Connect ForPost as MCP connector in Claude
2. Test all three modes end-to-end
3. First real task: deploy BriefDay v0 through ForPost
