# ForPost

## What this is

A minimal MCP server giving Claude a remote command-line tunnel into a VPS.
Primary tool `execute_command` plus explicit file ops (`write_file` /
`read_file` / `delete_file`), three modes, API key auth, full logging.
ForPost executes and returns - no smart logic, no retries, no rollback.
All reasoning about what to run and how to interpret results happens on
the Claude/user side.

## Core principles

- Simplicity over features. If in doubt, don't add it.
- ForPost never edits files on the VPS directly outside its own scope -
  all project code lives in GitHub, VPS only runs `git pull` + deploy.
- Two separate log streams: `commands.jsonl` (audit trail of what was run)
  and `app.log` (ForPost's own operational logs). Don't mix them.
- No database. Logs are JSONL files. No Postgres/Mongo/vector DB for this.
- No IP allowlisting, no OAuth, no multi-user - single static API key
  is the entire auth model. This is intentional for a personal tool.

## Modes

- `read-only`: read/inspect commands only, writes rejected
- `whitelist`: command checked against a config file list on VPS
- `full-access`: anything goes, single request, no confirmation step,
  fully logged regardless

## Stack

Ubuntu-based Docker container, non-root `forpost` user in `docker` group,
bash/git/gh/docker-compose, FastAPI + MCP wrapper, Caddy/nginx for HTTPS.

## Deployment status

Phases 1-4 done, Phase 5 in progress. Live at `https://forpost.poly-mind.net`
(German Zomro VPS `188.137.227.248`, code in `/opt/forpost`) behind the shared
`LibreChat-NGINX` proxy on the `librechat_default` network. The app serves both
REST (`/health`, `/execute`, file ops) and the MCP streamable-HTTP endpoint at
`/mcp` (guarded by `X-API-Key`). Remaining Phase 5: register the Claude custom
connector (`/mcp/` + `X-API-Key` header) and run the end-to-end mode tests.
Cert renew is manual - see `PLAN.md`.

## Local Development Environment

During development (Phase 1-2), ForPost runs locally, not on the VPS.
Create a local `.env` with dummy/placeholder values (e.g.
`API_KEY=dev-local-key`, `GITHUB_TOKEN=` left empty unless gh-dependent
code is being tested). Never ask for or use real VPS credentials during
local development - real secrets only exist in `.env` on the VPS itself
(Phase 4+).

## Testing

Every phase of the build plan must include tests before moving to the next
phase. For each new piece of functionality (auth, mode logic, execute_command,
logging), write tests that cover the happy path and the main failure modes
(missing/invalid API key, rejected command in read-only mode, command not
in whitelist, etc.). Run tests after each phase and fix failures before
proceeding. Use pytest.

## What NOT to do

- File ops (`write_file`/`read_file`/`delete_file`) DO exist - added per the
  SPEC "File Operations" revision (2026-06-15). They are simple byte transport
  scoped to `/home/forpost/`, not smart logic. Don't add tools beyond these +
  `execute_command` (the original "heredoc covers it" rule was superseded).
- Don't add auto-recovery, retries, or rollback logic
- Don't add a database for logs
- Don't add multi-step confirmation flows for Full Access
- Don't scope-creep toward Azure/AWS-style orchestration

## See also

`SPEC.md` for the full specification, build plan, and project tasks.
