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

1. **Get your API key** (never paste it into chats or screenshots):
   ```bash
   ssh root@<your-vps> "grep ^API_KEY= /opt/forpost/.env"
   ```
   Copy the value after `API_KEY=`.

2. **Add the connector** in Claude.ai → **Settings → Connectors → Add custom
   connector**:
   - **Name:** `ForPost`
   - **URL:** `https://forpost.poly-mind.net/mcp/`  *(keep the trailing slash)*
   - **Advanced → Request headers:** name `X-API-Key`, value = your key
   - **Save**

3. **Use it.** Open a new chat, enable the ForPost connector, and ask e.g.
   *"via ForPost, run `docker ps` in read-only"*.

4. **On your phone.** Nothing extra to do — connectors are **account-level**, so
   once added from any device they appear in the Claude mobile app (iOS/Android)
   automatically.

> If the connector dialog has no **Request headers** section, header auth (a
> beta feature) isn't enabled on your account yet — request access from
> Anthropic, or front `/mcp` with a small OAuth shim.

## Tools

Seven tools, exposed both as MCP tools (for the connector) and as REST endpoints
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

## Modes

Declared per call. They gate what a command is allowed to do:

| Mode | Behavior |
|------|----------|
| `read-only` | Inspection only (`ls`, `cat`, `docker ps/logs`, `git status`, `df`, `ps`…). Write commands **and** shell metacharacters (`; \| & \` $ ( ) < >`, newlines) are rejected. |
| `whitelist` | Only commands matching an entry in `whitelist.config` on the VPS; metacharacters likewise rejected. |
| `full-access` | Anything runs immediately, no gate. |

`read-only` and `whitelist` are guardrails — **protection from self**, not a
sandbox. They stop chaining a second command in (`ls; rm -rf`), reject secret
readers (`env`, `docker inspect`) and match on token boundaries (so `ps` does
not allow `psql`). But they do **not** neuter an allowed command's own dangerous
flags. Notes:

- **File writes/deletes require `full-access`** (a `whitelist` write + a
  whitelisted `docker compose up` could otherwise plant a malicious compose and
  escalate to the host). `read_file` is allowed in any mode.
- Executed commands run with the `API_KEY` **stripped** from their environment,
  so a command can't print the auth key. (`GITHUB_TOKEN` is kept for `gh`/`git`.
  Residual: `cat /proc/1/environ` of the app process can still see it — a
  narrower vector, closed only by removing it from the app env; not done yet.)

Use `full-access` consciously when you actually need pipes or chaining. All
three modes are fully logged.

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

Two independent layers:

1. **ForPost's own auth.** A single static API key, sent as the `X-API-Key`
   header, checked on **every** request (constant-time comparison) before
   anything runs. Missing/invalid → `401`, nothing executes or logs. The only
   unauthenticated route is `GET /health`. The key lives only in
   `/opt/forpost/.env` on the VPS (`chmod 600`); generate it with
   `openssl rand -hex 32`. No OAuth, no multi-user, no IP allowlist — the single
   key is the whole boundary. If it leaks: edit `.env`, `docker compose up -d`.

2. **The Claude connector.** Requires HTTPS + Streamable-HTTP (both provided),
   and passes the key via the `X-API-Key` request header (see Quick Start).

Additional layers on the VPS: nginx `limit_req`/`limit_conn` (flood &
brute-force protection, returns 429) and an app-level rate limit
(`RATE_LIMIT_PER_MINUTE`, default 60) covering both REST and `/mcp`.

> **Security note.** ForPost mounts the host Docker socket and runs as a user in
> the `docker` group — that's effectively root on the host. Combined with
> `full-access`, a leaked key means full host compromise. This is intentional
> for a single-user tool; keep the key secret and rotate on any suspicion.

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
| `GITHUB_TOKEN` | empty | Optional; only needed when `gh` is used inside the tunnel. |
| `PORT` | 8000 | Container listen port. |
| `RATE_LIMIT_PER_MINUTE` | 120 | App-level rate limit on actions (queries exempt). |
| `MAX_TIMEOUT_SECONDS` | 1800 | Hard ceiling a per-call `timeout` is clamped to. |
| `MAX_OUTPUT_CHARS` | 100000 | Cap on returned/logged output and a `read_file` slice. |
| `MAX_CONCURRENT_JOBS` | 5 | Max background jobs running at once. |
| `MAX_JOB_LOG_BYTES` | 52428800 | Per-async-job log size cap on disk. |
| `JOB_RETENTION_SECONDS` | 3600 | How long a finished job + its log is kept before reaping. |

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

1. Clone the repo to the VPS (e.g. `/opt/forpost`).
2. Copy `.env.example` → `.env`, set `API_KEY` (and `GITHUB_TOKEN` if needed).
3. Build & start, passing the host's docker group GID so the mounted socket is
   usable by the non-root `forpost` user:
   ```bash
   DOCKER_GID=$(getent group docker | cut -d: -f3) docker compose up -d --build
   ```
4. Put the service behind HTTPS on a subdomain (reverse proxy + Let's Encrypt).
5. Add the connector in Claude (see [Quick Start](#quick-start--connect-it-to-claude-and-your-phone)).

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

No multi-user auth / OAuth, no orchestration dashboard, no automated
rollback/snapshots, no database, no IP allowlist. A small personal tool, kept
small on purpose.

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 Vasili Holub.
