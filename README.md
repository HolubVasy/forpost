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
   - `API_KEY` - your ForPost auth key
   - `GITHUB_TOKEN` - dedicated bot account token
3. `docker compose up -d --build`
4. Point your domain at the container (reverse proxy + Let's Encrypt)
5. Add ForPost as an MCP connector in Claude using your domain + API key

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
