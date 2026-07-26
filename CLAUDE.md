# ForPost

Build rules and principles for working on this repo. See `README.md` for the
user guide and `SPEC.md` for the full specification and decision history.

## What this is

A minimal MCP server giving Claude a command-line tunnel into a VPS. It executes
and returns — no smart logic, no retries, no rollback; all reasoning about what
to run and how to read results happens on the Claude/user side.

Surface (both MCP tools and REST endpoints, sharing the same logic):
- `execute_command` — run a shell command synchronously.
- `run_async` / `job_status` / `job_kill` — background jobs for long commands
  (poll for progress + pagination).
- `write_file` / `read_file` / `delete_file` — byte transport within the
  workspace.

## Core principles

- Simplicity over features. If in doubt, don't add it.
- ForPost never edits files on the VPS directly outside its own scope — project
  code lives in GitHub; the VPS runs `git pull` + deploy.
- Two separate log streams: `commands.jsonl` (audit trail of what was run) and
  `app.log` (ForPost's own operational logs). Don't mix them.
- No database. Logs are JSONL files.
- Single static API key is the entire auth model (no OAuth, multi-user, or IP
  allowlist). Intentional for a personal tool. Compare it in constant time.
- The REST endpoints and the MCP tools must stay behaviourally identical — they
  reuse the same building blocks (`validate_command`, `execute`, `jobs`,
  `file_ops`, logging). Change the shared logic, not one surface.

## Modes (declared per call)

- `read-only` — inspection only; write commands **and** shell metacharacters
  (`; | & \` $ ( ) < >`, newlines) are rejected.
- `whitelist` — command must match `whitelist.config`; metacharacters likewise
  rejected.
- `full-access` — anything, immediately, fully logged.

Guardrails are "protection from self", not a sandbox: they don't neuter an
allowed command's own dangerous flags. `full-access` is the escape hatch.

## Stack

Ubuntu-based Docker container, non-root `forpost` user in the `docker` group,
bash/git/gh/docker-compose, FastAPI + FastMCP (streamable-HTTP at `/mcp`),
fronted by nginx for HTTPS.

## Deployment status

Phases 1–4 done; Phase 5 backend done. Live at `https://forpost.poly-mind.net`
(German Zomro VPS `188.137.227.248`, code in `/opt/forpost`) behind the shared
`LibreChat-NGINX` proxy on the `librechat_default` network. The app serves REST
(`/health`, `/execute`, file ops, jobs) and the MCP endpoint `/mcp` (guarded by
`X-API-Key` + rate limit). Only remaining: register the Claude custom connector
and run the end-to-end mode tests from chat. Cert renewal is manual — see
`README.md` / `SPEC.md`.

## Testing

Every change ships with tests; run `pytest` and keep it green before committing.
Cover the happy path and the main failure modes (missing/invalid key, rejected
command in read-only/whitelist, shell-injection, timeout, output cap, job
lifecycle). Executor/job tests use `sys.executable`, not a Windows-only `py`.

## What NOT to do

- Don't collapse or diverge the REST and MCP surfaces — keep both, backed by one
  shared implementation.
- Don't add tools beyond the current set without a clear need (the "one tool /
  no write_file / no polling" earlier rules were deliberately superseded by the
  file ops and async job model — extend consciously, not reflexively).
- Don't add auto-recovery, retries, or rollback logic.
- Don't add a database for logs.
- Don't add multi-step confirmation flows for `full-access`.
- Don't scope-creep toward Azure/AWS-style orchestration.
- Don't blind-reload the shared nginx — back up, `nginx -t`, then reload.

## See also

`README.md` (user guide, Quick Start, connector/phone setup), `SPEC.md` (spec +
decision history), `PLAN.md` (build phases).
