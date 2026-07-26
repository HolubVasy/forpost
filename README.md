# ForPost

A personal MCP server that gives Claude a secure command-line tunnel into your
VPS. Send a command and a mode; ForPost runs it on the server and returns
`stdout` / `stderr` / `exit_code` — from Claude chat, on **any device including
your phone**. It executes and returns: no smart logic, no retries, no rollback —
all reasoning stays on Claude's side.

**Status:** live at `https://forpost.poly-mind.net` — MCP endpoint
`https://forpost.poly-mind.net/mcp/`.

## Why ForPost

- **Manage infra from chat, anywhere.** Create GitHub repos, deploy Docker
  containers, read logs, restart services — from Claude on desktop or mobile.
  No SSH, no copy-pasting commands.
- **Safe by mode.** Every call declares `read-only` / `whitelist` /
  `full-access`. The first two reject writes *and* shell-injection, so you can
  hand Claude a real guardrail.
- **Long tasks, followed live.** `docker build` / deploys run as background
  jobs you poll for progress — no 5-minute wall, no lost output.
- **Minimal & auditable.** One small FastAPI service, a JSONL audit log, a
  single API key, no database.
- **Self-describing.** The MCP server ships its own manual and per-tool
  annotations, so any client discovers exactly what it can do.

## Quick Start — connect it to Claude (and your phone)

> Prerequisite: ForPost is deployed and reachable over HTTPS (see
> [Deployment](#deployment-vps)). The public instance is already up.

`/mcp` accepts **either** auth (see [Security](#security--authentication)):

- **A — Google sign-in (OAuth).** What the public instance uses. The connector
  dialog shows a *"Sign in with Google"* button; only allow-listed emails get in.
- **B — Header key (`X-API-Key`).** Simpler, but the connector dialog only offers
  a **Request headers** field on accounts with that beta enabled.

**Add the connector** in Claude → **Settings → Connectors → Add custom
connector**:
   - **Name:** `ForPost`
   - **URL:** `https://forpost.poly-mind.net/mcp/`  *(keep the trailing slash)*
   - **Path A (OAuth):** save, then click **Connect** → **Sign in with Google**
     with an allow-listed account.
   - **Path B (header):** **Advanced → Request headers** → name `X-API-Key`,
     value = your key (`ssh root@<vps> "grep ^API_KEY= /opt/forpost/.env"`; never
     paste it into a chat).

Then **use it**: open a new chat, enable the ForPost connector, and ask e.g.
*"via ForPost, run `docker ps` in read-only"* or *"via ForPost, show
diagnostics"*.

**On your phone:** nothing extra — connectors are **account-level**, so once
added (and, for OAuth, signed in) they appear in the Claude mobile app
automatically.

## Tools

Nine tools, exposed both as MCP tools (for the connector) and as REST endpoints
(for scripts/tests). They share the exact same logic.

| Tool | Purpose |
|------|---------|
| `execute_command(command, mode, timeout?)` | Run a shell command, wait, return `stdout`/`stderr`/`exit_code`/`timed_out`. |
| `run_async(command, mode, timeout?)` | Start a long command in the background → returns a `job_id` immediately. |
| `job_status(job_id, offset?)` | Poll a job: status, exit_code, and new output from `offset` (follow progress + paginate). |
| `job_kill(job_id)` | Terminate a running job. |
| `write_file_tool(path, content, mode, encoding?)` | Write a file in the workspace (`text` or `base64`). **full-access only.** |
| `read_file_tool(path, mode, encoding?, offset?, length?)` | Read a bounded slice of a file (any mode); paginate big files via `offset`/`next_offset`. |
| `delete_file_tool(path, mode)` | Delete a file. **full-access only.** |
| `diagnostics()` | One-call health snapshot: alive?, memory, jobs (running/stuck), tool versions, recent failures. Also `GET /diagnostics`. |
| `reset(scope)` | Reset own state: `scope`=`jobs` (kill+wipe jobs & their logs) / `logs` (truncate command+app logs) / `workspace` (delete scratch) / `all` (full cleanup). Also `POST /reset`. |

## Modes

Declared per call. They gate what a command is allowed to do:

| Mode | Behavior |
|------|----------|
| `read-only` | Local inspection only (`ls`, `cat`, `git status/log`, `df`, `ps`…). Write commands **and** shell metacharacters (`; \| & \` $ ( ) < >`, newlines) are rejected. No git token, no docker. |
| `whitelist` | Only commands matching `whitelist.config` on the VPS — `git pull/fetch/status/log` and read-only `gh pr list/view/diff`, `gh repo view`, `gh issue list/view`; metacharacters rejected. |
| `full-access` | Anything runs immediately, no gate — docker, pipes, writes, the personal git token. |

`read-only` and `whitelist` are guardrails — but, unlike a plain string filter,
they are backed by **privilege separation**: those commands run as a separate
unprivileged user (`forpost-ro`), so they can't read the app's environment
(`/proc/1/environ`), the API key, the audit log, or the docker socket. They also
reject command chaining (`ls; rm -rf`) and match on token boundaries (so `ps`
does not allow `psql`). Notes:

- **File writes/deletes require `full-access`** (a `whitelist` write + docker
  could otherwise plant a malicious compose and escalate). `read_file` is any mode.
- **Docker needs the socket** (root-equivalent), so `docker ps`/`docker logs`/
  compose are **full-access only** — they left read-only and the whitelist.
- **Git tokens are scoped by mode:** read-only gets none; whitelist gets the
  **work** token (read commands only → the work repo can't be written);
  full-access gets the **personal** token (full rights on your own repos).

Use `full-access` consciously — it's the root-equivalent escape hatch. All three
modes are fully logged.

## Long-running commands (async jobs)

`execute_command` is synchronous — it waits and returns the result, bounded by
its timeout. For anything that outlives a single request (`docker build`,
deploys), use the job tools instead:

- `run_async(command, mode, timeout)` returns a `job_id` at once.
- `job_status(job_id, offset)` returns the job's status and the output produced
  so far from `offset`. Poll it to follow progress; pass the previous
  `next_offset` next time (that offset also paginates a large output).
- `job_kill(job_id)` stops it.

The child process writes `stdout`+`stderr` straight to a per-job log file, so
job output never buffers in ForPost's memory. Job state is in-memory (lost if
the container restarts; the log files persist). MCP can't push partial output
mid-call, so following progress is by polling.

## Timeouts and output limits

- `execute_command` takes an optional per-call `timeout` (seconds, default 300),
  clamped to `MAX_TIMEOUT_SECONDS` (default 1800). On timeout the process is
  killed and partial output returns with `timed_out=true`.
- The command runs in a worker thread, so a long command never blocks the event
  loop or `/health`.
- Output is streamed into a fixed-size ring buffer capped at `MAX_OUTPUT_CHARS`
  (default 100000, tail kept) — a gigabyte-output command can't spike memory or
  fill the log.

## Security & authentication

A request to `/mcp` (or any REST route except `GET /health`) is accepted if it
satisfies **either** of two independent credentials — checked on **every**
request before anything runs; miss both → `401`, nothing executes or logs:

1. **Static API key.** Sent as the `X-API-Key` header, compared in constant time.
   The key lives only in `/opt/forpost/.env` on the VPS (`chmod 600`); generate
   it with `openssl rand -hex 32`. If it leaks: edit `.env`, `docker compose up
   -d`. This is the whole boundary for the REST surface.

2. **Google OIDC (optional, for the MCP connector).** Enabled with
   `OAUTH_ENABLED=true`. `/mcp` then also accepts a Google **bearer** token whose
   audience is `OAUTH_CLIENT_ID` and whose *verified* email is on
   `OAUTH_ALLOWED_EMAILS` (exact addresses or a whole `@domain`). ForPost is only
   a **resource server** — Google does the login; ForPost verifies the token
   (via Google's `tokeninfo`, cached ~5 min) and matches the allow-list, so
   arbitrary Google users can't get in. Discovery is standard RFC 9728: a `401`
   carries `WWW-Authenticate`, and `/.well-known/oauth-protected-resource`
   advertises the metadata. Turned off, ForPost stays key-only. Set
   `MCP_REQUIRE_OAUTH=true` to make `/mcp` **OAuth-only** — the key is then
   refused there (it still guards REST), so a leaked key can't drive the MCP
   tunnel; only an allow-listed Google account can.

**Privilege separation.** read-only/whitelist commands run as an unprivileged
user (`forpost-ro`) that can't read the app's environment, the API key, the
audit log, or the docker socket — so a restricted command cannot exfiltrate the
key and self-escalate. See [Modes](#modes).

**Read-only kill-switch.** `READ_ONLY_MODE=true` makes the server refuse
full-access, file writes/deletes and destructive reset entirely — only reading
runs. A safe posture while building trust; it also means a leaked key can do no
damage (nothing to escalate to). Set it to `false` when you want writes back.

Additional layers on the VPS: nginx `limit_req`/`limit_conn` (flood &
brute-force protection, returns 429) and an app-level rate limit
(`RATE_LIMIT_PER_MINUTE`, default 120) covering both REST and `/mcp`.

> **Security note.** ForPost mounts the host Docker socket and runs as a user in
> the `docker` group — that's effectively root on the host. Combined with
> `full-access`, a leaked key means full host compromise. This is intentional
> for a single-user tool; keep the key secret, rotate it **at least once a year**
> as routine hygiene, and immediately on any suspicion. (Key rotation is separate
> from the automatic, file-based log rotation.)

## Self-describing MCP

Any MCP client can discover ForPost's capabilities over the wire:

- `initialize` returns a usage manual in `instructions`.
- `tools/list` returns each tool's `title`, JSON Schema, and `annotations`
  (`readOnlyHint` / `destructiveHint` / `idempotentHint` / `openWorldHint`).
- `resources/list` + `resources/read` expose a `forpost://manual` markdown
  reference.

## Configuration (`.env`)

| Variable | Default | Purpose |
|----------|---------|---------|
| `API_KEY` | — (required) | Auth key (`openssl rand -hex 32`). |
| `GITHUB_TOKEN` | empty | Personal git token — exposed only in **full-access** (repo create, push, gh). |
| `GITHUB_WORK_TOKEN` | empty | Work git token — exposed only in **whitelist** (read commands), so the work repo can't be written. |
| `OAUTH_ENABLED` | `false` | Also accept a Google bearer on `/mcp` (resource-server verify + email allow-list). |
| `OAUTH_CLIENT_ID` | empty | Google OAuth client ID a bearer's audience must match. |
| `OAUTH_ALLOWED_EMAILS` | empty | Comma-separated allow-list: exact emails and/or `@domain`. |
| `MCP_REQUIRE_OAUTH` | `false` | Make `/mcp` OAuth-only (refuse `X-API-Key` there; needs `OAUTH_ENABLED`). |
| `READ_ONLY_MODE` | `false` | Kill-switch: refuse full-access, file writes/deletes and destructive reset — only reading. |
| `RESTRICTED_COMMAND_USER` | empty | User read-only/whitelist commands run as (set to `forpost-ro` by the image). |
| `PUBLIC_URL` | `https://forpost.poly-mind.net` | External base, published in the OAuth metadata. |
| `PORT` | 8000 | Container listen port. |
| `RATE_LIMIT_PER_MINUTE` | 120 | App-level rate limit on actions (queries exempt). |
| `MAX_TIMEOUT_SECONDS` | 1800 | Hard ceiling a per-call `timeout` is clamped to. |
| `MAX_OUTPUT_CHARS` | 100000 | Cap on returned/logged output and a `read_file` slice. |
| `MAX_CONCURRENT_JOBS` | 5 | Max background jobs running at once. |
| `MAX_JOB_LOG_BYTES` | 52428800 | Per-async-job log size cap on disk. |
| `JOB_RETENTION_SECONDS` | 3600 | How long a finished job + its log is kept before reaping. |
| `MAX_LOG_BYTES` | 10485760 | Per-file log budget; `commands.jsonl` over it drops its oldest lines, `app.log` rotates. |
| `LOG_TRIM_FRACTION` | 0.2 | Fraction of oldest `commands.jsonl` lines dropped when over budget. |

(`WORKSPACE_ROOT`, `LOGS_DIR`, `WHITELIST_PATH` are set by the container.)

## Local development

1. Create a virtualenv, `pip install -r requirements.txt`.
2. Copy `.env.example` to `.env`, fill with dummy values (e.g.
   `API_KEY=dev-local-key`) — no real secrets needed locally.
3. Run: `uvicorn app.main:app --reload`.
4. Test: `pytest`.
5. Manual smoke test:
   ```bash
   curl -X POST http://localhost:8000/execute \
     -H "X-API-Key: dev-local-key" -H "Content-Type: application/json" \
     -d '{"command": "echo hello", "mode": "read-only"}'
   ```

The MCP endpoint is served at `/mcp` by the same app. Run the MCP server
standalone with `python -m app.mcp_server` (stdio; set
`MCP_TRANSPORT=streamable-http` for HTTP).

## Deployment (VPS)

**GitHub is the source of truth — the VPS is a `git clone`, and every deploy is
`git pull` + rebuild. Never edit or copy files onto the VPS out of band.**

1. `git clone` the repo to the VPS (e.g. `/opt/forpost`).
2. Copy `.env.example` → `.env`, set `API_KEY` (and the git tokens if needed).
   `.env` is git-ignored and stays only on the VPS.
3. Build & start with the production compose (joins the shared proxy network),
   passing the host's docker group GID so the mounted socket is usable:
   ```bash
   DOCKER_GID=$(getent group docker | cut -d: -f3) \
     docker compose -f docker-compose.prod.yml up -d --build
   ```
4. Put the service behind HTTPS on a subdomain (reverse proxy + Let's Encrypt).
5. Add the connector in Claude (see [Quick Start](#quick-start--connect-it-to-claude-and-your-phone)).

**Redeploy** after any change is always: on the VPS `git pull`, then re-run the
step-3 command. (`docker-compose.yml` is the base/local file; `docker-compose.prod.yml`
is the VPS one, both tracked in git.)

The container runs `uvicorn app.main:app` on port 8000 as the non-root
`forpost` user (in the `docker` group), with `/home/forpost` on the
`forpost_home` volume. `GET /health` is an unauthenticated liveness check.

### This instance

Deployed on a German Zomro VPS in `/opt/forpost`. The container joins the
existing `librechat_default` Docker network so the shared **`LibreChat-NGINX`**
reverse proxy reaches it as `http://forpost:8000` and terminates TLS for
`forpost.poly-mind.net`. The proxy config is a single host file,
`/opt/LibreChat/client/nginx.conf`. Its forpost vhost adds:
`limit_req`/`limit_conn` (429 on flood), and `proxy_read_timeout` /
`proxy_send_timeout` of **1800s** (nginx's 60s default would `504` a long
synchronous command before the app finished — verified a 65s command returns
200).

> **Cert renewal is manual.** The Let's Encrypt cert is `certbot standalone`,
> which needs port 80 — held by the nginx container — so the scheduled
> `certbot renew` fails. Renew before expiry with:
> `docker stop LibreChat-NGINX && certbot renew && docker start LibreChat-NGINX`

When editing the shared nginx config: back it up, `docker exec LibreChat-NGINX
nginx -t`, then `nginx -s reload` (graceful). Never blind-reload — this proxy
fronts other live services.

## Logs

- `commands.jsonl` — audit trail: every command/file-op, with mode, exit_code,
  output (truncated to the output cap).
- `app.log` — service-level operational logs (startup, auth failures, errors).

## Out of scope (by design)

No multi-user accounts or roles (OAuth is just an email allow-list gating a
single-user tool), no orchestration dashboard, no automated rollback/snapshots,
no database, no IP allowlist. A small personal tool, kept small on purpose.

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 Vasili Holub.
