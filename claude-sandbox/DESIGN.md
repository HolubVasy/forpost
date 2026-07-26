# claude-builder — design & decision record

The "why" behind the build yard. `CLAUDE.md` is the rules, `README.md` is the
user guide; this file records the decisions and the alternatives we rejected.

## 1. Problem

Driving development on the VPS through ForPost alone means shuttling files and
logs by hand: send a change → deploy → it fails → read logs → paste to the
fixer → fix → redeploy → … The human is the relay, and it's endless round-trips.
Worse, a fixer with no runtime can't even tell if a solution *runs* (needs DB,
Kafka, …), so "done" doesn't mean "works".

Goal: **from the phone, give a prompt; an agent builds and proves a real,
running solution and hands back one PR (code + report); you review and deploy.**
The human is not the relay.

## 2. Architecture

```
you (phone) ──prompt──▶ master (chat-Claude)        the brain: decides, reviews,
                              │                       runs the fix loop
                              │ via ForPost
                              ▼
                        ForPost (courier)            relays the prompt, checks
                              │                       status, deploys a slot
                              ▼
                        claude-builder (hands)       clone → code → compose up
                                                      (own rootless docker) →
                                                      test → logs → fix → PR
```

- The builder does **all** business from the prompt; ForPost writes no code.
- git is the source of truth; every result is a PR.

## 3. Decisions

### D1 — The builder gets its own docker → **rootless dind** (not privileged, not host socket)
- **Context:** to prove a solution works, the builder must `docker compose up`
  the whole stack (app + DB + Kafka + …). Number/type of services is irrelevant —
  it's one uniform `compose up`. So the only requirement is: the builder has
  docker.
- **Decision:** give the builder **rootless docker-in-docker**.
- **Why:** the host socket = root on the host (rejected). Privileged dind can
  **escape to the host** — the one real hole. Rootless dind runs docker via user
  namespaces **without `--privileged`**, closing the escape while keeping full
  `compose up` capability.
- **Rejected:** host socket (breaks isolation); privileged dind (host escape);
  sysbox (needs a custom runtime on the host — more infra than a lab wants).

### D2 — Exposure → **fixed slots `alpha1|alpha2|alpha3.poly-mind.net`** (not wildcard)
- **Context:** deployed solutions need URLs; wildcard `*.poly-mind.net` needs a
  wildcard TLS cert, which forces DNS-01 (Namecheap API — fiddly).
- **Decision:** a fixed pool of three **neutrally-named** slots (`alphaN` — a slot
  is a reusable container, its content changes; naming a slot after a solution
  would lie once it's reused), one **SAN cert via HTTP-01**.
- **Why:** named subdomains validate over HTTP-01 (no DNS API). Bounded resource
  use; nginx has three static blocks; ForPost just swaps what runs in a slot.
  Three A-records on the existing domain — no new domains bought.
- **Rejected:** wildcard + DNS-01 (hassle); a port per solution (not
  phone-friendly); themed per-solution names (grow unbounded → the "помойка"
  we're avoiding; and lie when a slot is reused).

### D3 — Secrets → **dummy for the builder's test, real at prod deploy**
- The builder brings the stack up with **throwaway** credentials (`.env.test`);
  **real** secrets are injected by ForPost at deploy. The repo carries only
  `.env.example`. A DB-backed POC is required precisely to exercise this.

### D4 — Verification line → **builder self-test is the gate; no separate dev env**
- The builder proving the real stack in its own docker *is* the verification.
  A separate ForPost "dev deploy" would be a redundant extra loop.
- ForPost does only a **health-check after the prod deploy**. "ForPost dev" was
  considered and **dropped**.

### D5 — Output → **PR = report**
- Every PR description carries: what, why, how verified (compose up + tests +
  `/health` + log excerpts), and open questions. The PR is the single pane of
  glass so the human never chases logs or status.

### D6 — The fix loop is run by the **master**, not the human
- When a deploy/verify fails, the **master (chat-Claude)** feeds the logs back to
  the builder and re-runs — autonomously, against DEV — until green or N tries,
  then surfaces the final PR. The human only sets the task and approves prod.
- Round-trips don't vanish (fixing bugs iterates), but the human stops being the
  relay and the loop shrinks (self-test catches most before deploy).

### D7 — One builder, ephemeral per-task workspaces
- One `claude-builder` for all tasks; each task in a fresh `/work/task.XXXX`,
  auto-pruned. Reproducible (always latest git), trivially cleanable.

## 4. Security model — holes and mitigations

| Hole | Risk | Mitigation |
|------|------|------------|
| docker in the builder | host escape | **rootless dind**, no `--privileged`, no host socket, no host mounts |
| builder-authored code | bad code to prod | **PR-gate** + branch protection; deploy only on command |
| builder-authored compose | dev container escapes host | **ForPost sanitizes** compose: reject `privileged`, `docker.sock`, host mounts, `cap_add`, host network |
| dev/prod share the host | noisy neighbour / lateral move | resource limits + a **separate network** for dev slots |
| tokens | work repo written / key leak | **personal git token only**; builder has no host/prod reach |

Accepted, honestly: the builder runs untrusted-ish generated code; the guard is
that it's sandboxed (rootless), and code reaches prod only through your review.

## 5. Lifecycles

- **Test stack** (app+db to verify): inside the builder's rootless docker;
  brought up and torn down **by the builder**, per task; ephemeral; never exposed.
- **Deploy slot** (`alphaN.poly-mind.net`): on the host, deployed and freed **by
  ForPost** on command; persistent (a running service you use); max three.

## 6. Out of scope

- No second "master" agent — the master is the chat-Claude + you.
- No auto-deploy to prod without review.
- No wildcard TLS / DNS-01.
- No per-solution manual infra — slots are fixed and reused.

## 7. Status / rollout

1. **Now:** builder container (subscription auth, non-root), `task`/`clean`,
   opt-in compose. *(built)*
2. **Next:** rootless dind so the builder brings up full stacks itself.
3. **Then:** PR-as-report prompt convention; a DB-backed POC (app + Postgres)
   that exercises D3.
4. **Then:** ForPost deploy into `alphaN.poly-mind.net` slots with compose
   sanitization; the master-driven fix loop (D6).
