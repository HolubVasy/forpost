# claude-builder — an autonomous build yard on your VPS

Give it a prompt through ForPost from your phone; it builds a solution, brings up
the **whole stack in its own rootless docker** to prove it works, and pushes a
**PR that is also a report**. You review and deploy. You are never the relay.

Two natures, not a contradiction:

- **By security — a sandbox:** rootless docker, no host socket, no host mounts,
  non-root user → its blast radius is this one container. Host, prod, and the
  work repo are unreachable.
- **By purpose — a build yard:** where real solutions get built to a disciplined
  process (git is the source of truth, one fresh checkout per task, PR-gated
  deploys). Not a playground.

## The three roles

```
you (phone)
   │  a prompt
   ▼
master = the chat-Claude       ← decides, reviews, runs the fix loop
   │  docker exec via ForPost
   ▼
ForPost = dumb courier         ← relays the prompt, checks status, deploys
   │
   ▼
claude-builder = the hands     ← clone → code → (rootless) compose up → test →
                                  read logs → fix → push a PR (code + report)
```

The builder does **all** the business from the prompt (clone/create repo, code,
test, push, PR). ForPost never writes code — it only carries the string and
checks `job_status`.

## The flow (everything through ForPost, phone-friendly)

1. **You:** "build X" (a prompt).
2. **master → ForPost `run_async`:** `docker exec claude-builder task "<prompt>"`
   → `job_id`.
3. **builder:** clones/creates the repo, writes code, **`docker compose up` the
   full stack in its own rootless docker**, runs integration tests, reads its own
   logs, fixes until green — then opens a **PR whose description is a report**
   (what / why / how verified with log evidence / open questions).
4. **You (phone):** read the one PR — code + report + proof it runs. Fixes loop
   autonomously inside the builder, so you only see the final good PR.
5. **You:** approve → **ForPost deploys** the solution into a slot
   `alphaN.poly-mind.net` + a health-check.

Because the builder proves the real stack itself, "PR" means "it came up, logs
are clean" — that is what removes the deploy → read-logs → relay → fix loop.

## One-time setup

1. On **your own machine**, mint a subscription token (browser login is local,
   no Anthropic API key, no per-token billing):
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
3. Build & start the builder (opt-in; separate from the ForPost deploy):
   ```bash
   docker compose -f docker-compose.builder.yml up -d --build
   ```

## Use it — through ForPost, no terminal

```
# ask (master relays this via ForPost, full-access, run_async):
docker exec claude-builder task "create private repo todo-api, build a FastAPI +
Postgres todo service with integration tests, bring the stack up and prove it
works, then open a PR with a summary"
# check:  ForPost job_status(job_id) -> status + output;  the PR notifies you
```

## Deploy slots (where solutions run)

A **fixed pool** `alpha1|alpha2|alpha3.poly-mind.net` — one SAN cert via HTTP-01 (no
wildcard, no DNS-01 hassle). Names are neutral and reusable (a slot is a
container; the solution inside it changes). ForPost deploys a solution into a
free slot and frees it on command. Up to three solutions live at once. Test
stacks the builder brings up are **ephemeral inside its rootless docker** and
never touch the pool.

## Cleanup

- **Builder tasks:** each in a fresh `/work/task.XXXX`; the wrapper prunes dirs
  older than `TASK_RETENTION_DAYS` on each run — no cron. On demand:
  `docker exec claude-builder clean`.
- **Test stacks:** ephemeral; the builder tears them down when a task ends.
- **Deploy slots:** persistent (they're running services); ForPost frees a slot
  on command.

## Security (see also `CLAUDE.md`)

- **rootless dind** (not privileged) → no host escape.
- **PR-gate + branch protection** → no unreviewed code reaches prod.
- **ForPost sanitizes a solution's compose** (rejects `privileged`,
  `docker.sock`, host mounts, `cap_add`, host network) before deploying.
- Dev slots: resource limits + their own network, separate from prod.
- Personal git token only; the work repo is unreachable.

## Status

Built now: the builder container (Claude Code, non-root, subscription auth), the
`task` wrapper (headless, fresh dir per task, auto-prune), `clean`, the opt-in
compose.

Planned (design agreed, see `CLAUDE.md`): rootless dind so the builder brings up
full stacks itself; the PR-as-report prompt convention; ForPost deploy into
`alphaN.poly-mind.net` slots with compose sanitization; the master-driven fix loop.
