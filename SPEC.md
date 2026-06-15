# ForPost - Specification v1.1 (Final)

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
- Rotation: on suspected leak, or as occasional hygiene - no fixed schedule.

## Logging

Two separate log streams:

- **Command logs** (`logs/commands.jsonl`) - every execute_command call:
  timestamp, mode, command, exit_code, output. Audit trail of what was run.
- **Application logs** (`logs/app.log`) - ForPost's own runtime logs
  (startup, errors, auth failures, crashes). Operational/debugging data,
  kept separate from the command audit trail.

No database. JSONL files. Rotation via `logrotate` if/when it becomes
relevant - not configured now.

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
- No multi-user auth, OAuth, token rotation system
- No automated rollback/snapshot system
- No IP allowlisting (API key is sufficient)
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

## Status

Spec complete. Ready to build.

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
3. Configure domain + Let's Encrypt HTTPS (reverse proxy: Caddy or nginx)
4. Smoke test: read-only `docker ps` from outside

### Phase 5: Integration
1. Connect ForPost as MCP connector in Claude
2. Test all three modes end-to-end
3. First real task: deploy BriefDay v0 through ForPost
