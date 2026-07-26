# claude-builder — build rules

Principles for the autonomous build yard. See `README.md` for the user guide,
and the repo root `CLAUDE.md` / `SPEC.md` for ForPost itself.

## What this is

An isolated Claude Code container (the **builder**) that turns a prompt into a
reviewed, deployed solution. You drive it **only through ForPost** — you have no
direct access to it. Three roles:

- **master** — the chat-Claude you talk to (on your phone). Decides, plans,
  reviews the PR, and orchestrates the fix loop. The brain.
- **builder** — this container. Does the work: clones/creates a repo, writes
  code, brings up the **full stack in its own rootless docker**, runs tests,
  reads logs, fixes, and pushes a PR. The hands that build.
- **ForPost** — a dumb courier: relays the prompt to the builder, checks
  `job_status`, and deploys the result to a slot. No logic of its own.

## Core principles

- The builder does **all** the business from the prompt; ForPost never writes
  business code (no `write_file` of solutions).
- **git is the source of truth.** Every result is a **PR = code + report**.
- The builder **verifies the real stack itself** (rootless docker: `compose up`
  → integration → logs) *before* pushing — so "PR means it came up, logs clean".
  This is what kills the round-trips.
- **One builder for all tasks.** Each task runs in a fresh `/work/task.XXXX`;
  old ones are auto-pruned. Number/type of services in a compose is irrelevant —
  it's just infra, one `docker compose up`.
- **Personal git token only — never the work token.** The builder must be unable
  to touch work repos.
- The **master runs the loop** (deploy → logs → fix → redeploy), not the human.
  You are not a relay.

## Security model (the holes, and how they're closed)

- **Isolation:** no host docker socket, no host mounts, non-root user. The
  builder cannot reach host/prod containers directly.
- **rootless dind (NOT privileged):** the builder runs docker *without*
  `--privileged`, via user namespaces — so a container escape to the host is
  closed. Privileged dind was the one real hole; rootless is the decision.
- **Untrusted code:** the builder's output is code → **PR-gated**. Deploy only on
  command, with branch protection on `main`. Review before deploy.
- **Untrusted compose:** a solution's compose is builder-authored →
  **ForPost sanitizes it before deploying** (reject `privileged`,
  `/var/run/docker.sock`, host bind-mounts, `cap_add`, host network).
- **Shared host:** dev slots get resource limits and their own docker network,
  separate from prod.

## PR = report

Every PR description must carry, so the human never has to chase anything:

- **What** was done (summary).
- **Why** (key decisions / tradeoffs).
- **How verified** (compose up result, integration tests, `/health`, log
  excerpts — evidence, not "trust me").
- **Open questions** for the master, if a serious decision is needed — here, not
  in a side channel.

## Deploy slots

Solutions run in a **fixed pool** `alpha1|alpha2|alpha3.poly-mind.net` (one SAN cert
via HTTP-01 — no wildcard, no DNS-01). Names are neutral and reusable: a slot is
a container, its content changes; ForPost deploys a solution into a free slot and
frees it on command. Slots are the only externally-exposed surface.

## What NOT to do

- Don't give the builder the host docker socket or host mounts.
- Don't run dind privileged — rootless only.
- Don't auto-deploy unreviewed code to prod.
- Don't deploy a solution compose without sanitizing it.
- Don't put the work token in the builder.
- Don't make ForPost smart — it stays a courier.
