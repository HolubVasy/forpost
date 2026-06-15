# ForPost

## What this is

A minimal MCP server giving Claude a remote command-line tunnel into a VPS.
One tool (`execute_command`), three modes, API key auth, full logging.
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

- Don't add `write_file` as a separate tool - heredoc via execute_command covers it
- Don't add auto-recovery, retries, or rollback logic
- Don't add a database for logs
- Don't add multi-step confirmation flows for Full Access
- Don't scope-creep toward Azure/AWS-style orchestration

## See also

`SPEC.md` for the full specification, build plan, and project tasks.
