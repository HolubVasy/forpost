# ForPost

A personal MCP server that gives Claude a command-line tunnel into your VPS.

## What it does

Send a command and a mode, ForPost runs it and returns the result.
That's the whole thing. Used for: creating GitHub repos, deploying
Docker containers, reading logs, restarting services - all from chat,
on any device.

## Modes

| Mode | Behavior |
|------|----------|
| `read-only` | Inspection only. Write commands rejected. |
| `whitelist` | Command must match an entry in `whitelist.config`. |
| `full-access` | Anything runs, immediately, no confirmation. |

## Local Development

1. Create a Python virtualenv, `pip install -r requirements.txt`
2. Copy `.env.example` to `.env`, fill with dummy values (e.g.
   `API_KEY=dev-local-key`) - no real secrets needed locally
3. Run the service: `uvicorn app.main:app --reload`
4. Run tests: `pytest`
5. Smoke test manually:
   ```
   curl -X POST http://localhost:8000/execute \
     -H "X-API-Key: dev-local-key" \
     -H "Content-Type: application/json" \
     -d '{"command": "echo hello", "mode": "read-only"}'
   ```

## Setup (VPS / Production)

1. Clone this repo to your VPS
2. Copy `.env.example` to `.env`, fill in:
   - `API_KEY` - your ForPost auth key (`openssl rand -hex 32`)
   - `GITHUB_TOKEN` - dedicated bot account token (optional; only needed when
     `gh` is used inside the tunnel - can be left empty at first)
3. Build and start, passing the host's docker group GID so the mounted
   socket is usable by the non-root `forpost` user:
   ```
   DOCKER_GID=$(getent group docker | cut -d: -f3) docker compose up -d --build
   ```
4. Point your domain at the container (reverse proxy + Let's Encrypt)
5. Add ForPost as an MCP connector in Claude using your domain + API key

The container runs the FastAPI service (`uvicorn app.main:app`) on port 8000
as the non-root `forpost` user, in the `docker` group, with `/home/forpost`
persisted on the `forpost_home` volume. `GET /health` is an unauthenticated
liveness check (used by the container HEALTHCHECK and any reverse proxy).

## This instance

Deployed at `https://forpost.poly-mind.net` (German Zomro VPS, `/opt/forpost`).
The container joins the existing `librechat_default` Docker network so the
shared `LibreChat-NGINX` proxy reaches it as `http://forpost:8000`; TLS is a
Let's Encrypt cert issued via certbot **standalone**.

> **Cert renewal is manual.** The standalone cert needs port 80, which the
> nginx container holds, so scheduled `certbot renew` fails. Renew before the
> expiry with:
> `docker stop LibreChat-NGINX && certbot renew && docker start LibreChat-NGINX`

## Logs

- `logs/commands.jsonl` - every command executed, with mode/exit_code/output
- `logs/app.log` - service-level operational logs

## Security

Single API key, checked on every request. Whitelist mode adds a config-file
allowlist on top. Full access mode has no extra gate - use read-only or
whitelist when you want a guardrail.

## Out of scope

No multi-user auth, no orchestration dashboard, no automated rollback,
no database. This is a small personal tool, kept small on purpose.
