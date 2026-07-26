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

## Reverse proxy (nginx)

ForPost does **not** expose a host port of its own. All external traffic goes
through the one shared reverse proxy on the box, the `LibreChat-NGINX`
container, which terminates TLS for every subdomain (`chat`, `jobs`, `auto`,
`forpost`). Its config is a single file on the host,
`/opt/LibreChat/client/nginx.conf`, bind-mounted into the container at
`/etc/nginx/conf.d/default.conf`.

`forpost.poly-mind.net` is served by two appended `server` blocks:

```nginx
server {                                   # HTTP -> HTTPS redirect
    listen 80;
    server_name forpost.poly-mind.net;
    location / { return 301 https://$host$request_uri; }
}
server {                                   # TLS termination + proxy
    listen 443 ssl http2;
    server_name forpost.poly-mind.net;
    ssl_certificate     /etc/letsencrypt/live/forpost.poly-mind.net/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/forpost.poly-mind.net/privkey.pem;
    client_max_body_size 50M;
    resolver 127.0.0.11 valid=30s;         # docker embedded DNS
    set $forpost_upstream http://forpost:8000;
    location / {
        proxy_pass $forpost_upstream;      # variable -> nginx starts even if
        proxy_http_version 1.1;            #   forpost is down
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header Upgrade $http_upgrade;    # WebSocket / streaming
        proxy_set_header Connection "upgrade";
    }
}
```

Notes:
- nginx reaches the app **by container name** (`http://forpost:8000`), which
  only resolves because the forpost container is attached to the
  `librechat_default` network (see `docker-compose.yml`).
- `proxy_pass` uses a variable + `resolver` (the same trick the `auto`
  subdomain uses) so a restart of forpost doesn't take nginx's whole config
  down with a "host not found in upstream" error.
- `location /` proxies **all** paths, so once the MCP endpoint (`/mcp`) is
  live it is reached through the exact same block - no nginx change needed.
- After editing the config: back it up, run `docker exec LibreChat-NGINX nginx
  -t`, then `docker exec LibreChat-NGINX nginx -s reload` (graceful, no
  downtime). Never blind-reload without `nginx -t` - this proxy fronts other
  live services.

## Logs

- `logs/commands.jsonl` - every command executed, with mode/exit_code/output
- `logs/app.log` - service-level operational logs

## Authentication

Two layers, and they are independent:

**1. ForPost's own auth (the app).** A single static API key, sent as the
`X-API-Key` request header, checked on **every** request before anything else.
Missing or wrong key -> `401`, nothing executes, nothing is logged to
`commands.jsonl`. The same key covers all three modes. The only unauthenticated
route is `GET /health` (so proxies / healthchecks don't need the key). The key
lives only in `/opt/forpost/.env` on the VPS (`chmod 600`) - generate it with
`openssl rand -hex 32`. There is deliberately no OAuth, no multi-user, no IP
allowlist: the single key is the whole security boundary. If it leaks, rotate
it (edit `.env`, `docker compose up -d`).

**2. Connecting Claude (the MCP connector).** Claude custom connectors require:
- **HTTPS with a valid cert** - provided by nginx above.
- **Streamable HTTP transport** at an MCP endpoint (e.g. `/mcp`) - this is what
  `app/mcp_server.py` serves; wiring it into the deployed container is Phase 5.
- **Auth passed by Claude.** The connector UI supports either OAuth 2.0 or a
  static **request header** (beta, allowlisted header names). ForPost's model
  maps to the header option: configure the connector to send
  `X-API-Key: <your key>`. If header auth isn't available on your account, the
  fallback is to put a small OAuth shim in front, or expose `/mcp` unauthenticated
  and rely on an unguessable path + the reverse proxy (weaker - prefer the header).
- Once configured on the account, the connector works on **all** Claude
  clients including mobile - no per-device setup.

## Modes as guardrails

`read-only` and `whitelist` are the guardrails; `full-access` has no extra gate
(anything runs immediately). Choose read-only or whitelist when you want a brake.
All three are fully logged regardless.

## Out of scope

No multi-user auth, no orchestration dashboard, no automated rollback,
no database. This is a small personal tool, kept small on purpose.
