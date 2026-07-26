# claude-sandbox — autonomous coding agent (POC)

A sandboxed [Claude Code](https://code.claude.com) container. You give it a repo
and a goal through ForPost; it clones, edits, tests, fixes, and **pushes a
branch**. It never touches the host — no docker socket, no host mounts — and it
runs on your **Claude subscription**, not an API key.

Division of labour:

- **claude-runner** (this) = the workbench: `clone → code → test → git push`.
- **GitHub** = the hand-off (source of truth).
- **ForPost** = deploys the *solution* as its own clean container from git.

## One-time setup

1. On **your own machine**, mint a subscription token (browser login is local):
   ```bash
   claude setup-token
   ```
   Copy the printed token.
2. On the **VPS**, create `.runner.env` (git-ignored) next to the compose files:
   ```
   CLAUDE_CODE_OAUTH_TOKEN=<the token from step 1>
   GITHUB_TOKEN=<your PERSONAL github token>   # never the work token
   CLAUDE_MAX_TURNS=40
   ```
3. Build & start the sandbox (opt-in; separate from the ForPost deploy):
   ```bash
   docker compose -f docker-compose.runner.yml up -d --build
   ```

## Use it (through ForPost, full-access)

Run a task and follow it as a background job:

```
docker exec claude-runner task "clone github.com/<you>/<repo>, add GET /health
returning {ok:true} with a test, make tests pass, push branch feat/health"
```

Then review the branch/PR on GitHub, merge, and let ForPost deploy from git.

## Guardrails

- Non-root user (`bypassPermissions` refuses root), no docker socket, no host
  mounts — blast radius is this container.
- Personal git token only; the work token is never present, so the agent can't
  write to work repos.
- `--max-turns` caps a runaway loop. Long tasks: drive via ForPost `run_async` +
  `job_status`.
