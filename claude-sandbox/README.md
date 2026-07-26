# claude-sandbox — isolated build yard for an autonomous coding agent

A [Claude Code](https://code.claude.com) container that turns a **prompt into a
pushed PR**. Two natures, not a contradiction:

- **By security — a sandbox:** no docker socket, no host mounts, non-root user →
  its blast radius is this one container. It cannot touch the host, prod, or the
  work repo.
- **By purpose — a build yard / workshop:** where real solutions get built to a
  disciplined process (git is the source of truth, one fresh checkout per task,
  PR-gated deploys). Not a playground.

**ForPost is only a courier:** it relays your prompt to the agent and later
checks status. It never writes business code. The agent does everything from the
prompt — `git clone` / `gh repo create` / code / tests / `git push` / open PR.

```
you (phone) → ForPost run_async: docker exec claude-builder task "<prompt>"
                                   → agent: clone/create → code → test → push → PR
you (phone) → ForPost job_status(job_id)   → running / done / error + output
              → GitHub notifies you of the PR → review → "ForPost, deploy"
```

## One-time setup

1. On **your own machine**, mint a subscription token (browser login is local):
   ```bash
   claude setup-token
   ```
2. On the **VPS**, create `.builder.env` (git-ignored) next to the compose files:
   ```
   CLAUDE_CODE_OAUTH_TOKEN=<the token from step 1>
   GITHUB_TOKEN=<your PERSONAL github token>   # never the work token
   CLAUDE_MAX_TURNS=40
   TASK_RETENTION_DAYS=2
   ```
3. Build & start the one yard (opt-in; separate from the ForPost deploy):
   ```bash
   docker compose -f docker-compose.builder.yml up -d --build
   ```

## Use it — everything through ForPost (no terminal, phone-friendly)

Fire a task as a background job and follow it:

```
# ask:
docker exec claude-builder task "create private repo todo-api, build a FastAPI
todo service with tests, push, and open a PR with a short summary"
# check:  ForPost job_status(job_id) -> status + output;  PR notifies your phone
```

## Cleanup

- **Automatic:** each task runs in a fresh `/work/task.XXXX`; the wrapper prunes
  task dirs older than `TASK_RETENTION_DAYS` (default 2) on each run — no cron.
- **On demand:** `docker exec claude-builder clean` wipes all task workspaces.

## Guardrails (why there's no bungling)

- **git = source of truth** — the solution lives in a repo, not local edits.
- **fresh checkout per task** — no accumulated state; always latest git.
- **PR gate** — you review before anything is deployed to a runtime.
- **isolation** — non-root, no docker socket, no host mounts; work repo
  unreachable (personal git token only, never the work token).
- **`--max-turns` + auto-clean** — no runaway loop, no disk creep.
- **audit** — the ForPost job output + the PR summary show what and why.
