# Deploy slots — nginx config & one-time bootstrap

Fixed pool `alpha1|alpha2|alpha3.poly-mind.net`. These files live in **our git**;
the shared `LibreChat-NGINX` proxy just **includes** them. After the one-time
bootstrap below, all slot changes ride via `git pull` — no hand-editing on the VPS.

## Files here

| File | Role | Where it mounts in the nginx container |
|------|------|----------------------------------------|
| `forpost-slots-http.conf` | port 80: ACME challenge + redirect | `conf.d/` (include **first**) |
| `forpost-slots.conf` | port 443: the three slots + fallback | `conf.d/` (include **after** the cert exists) |
| `html/slot-empty.html` | "slot free" page for an empty slot | `/usr/share/nginx/html/forpost-slots/` |
| `librechat-nginx.example.yml` | reference: the shared nginx service with our mounts wired in | — (copy the marked lines once) |

## How a slot works (no nginx rewrites on deploy)

A slot is a **parking spot** (name + route + network), not an image. `forpost-slots.conf`
uses `resolver` + a variable `proxy_pass`, so nginx resolves `alphaN` **per request**:

- **Empty slot** (no container named `alphaN`) → `proxy_pass` 502s → `error_page`
  → static `slot-empty.html`.
- **Occupied** → bring up a container **named `alphaN`, listening on `:8080`, on the
  shared network** → the same block proxies to it immediately.

So ForPost **occupies** a slot with `docker compose -p alphaN up -d` and **frees** it
with `docker compose -p alphaN down`. The nginx config is **never** edited or reloaded
on deploy — that respects the repo rule "don't blind-reload the shared nginx".

> **Container contract:** the solution's container must (a) be named `alpha1`/`alpha2`/`alpha3`,
> (b) listen on `8080`, (c) join the shared network (`librechat_default`). ForPost's
> compose-sanitizer enforces the safety rules (no `privileged`, no `docker.sock`,
> no host mounts, no `cap_add`, no host network) before bringing it up.

## One-time bootstrap (on the VPS)

Two things depend on the LibreChat setup — **confirm these first**:

- **[A] conf.d path** — where `LibreChat-NGINX` includes site configs
  (e.g. `/opt/LibreChat/nginx/conf.d/`, mounted to `/etc/nginx/conf.d/`).
- **[B] certbot method** — how LibreChat issues certs today (a `certbot` container
  with a `/var/www/certbot` webroot, or `certbot --nginx`). Match it.

Golden rule for every reload: **backup → `nginx -t` → reload**, never blind.

### 1. Mount our files into the shared nginx

In the LibreChat compose service for nginx, add (paths per **[A]**):

```yaml
    volumes:
      - /opt/forpost/nginx/forpost-slots-http.conf:/etc/nginx/conf.d/forpost-slots-http.conf:ro
      # forpost-slots.conf added in step 4 (needs the cert first)
      - /opt/forpost/nginx/html:/usr/share/nginx/html/forpost-slots:ro
      - certbot_www:/var/www/certbot            # shared with the certbot webroot
```

`/opt/forpost` is the git checkout, so editing these files = `git pull`.

### 2. Enable the HTTP block, validate, reload

```bash
docker exec LibreChat-NGINX nginx -t          # MUST pass
docker exec LibreChat-NGINX nginx -s reload
curl -I http://alpha1.poly-mind.net           # 301 -> https (cert not issued yet)
```

### 3. Issue the SAN cert (HTTP-01, one cert for all three)

Webroot method (adjust to **[B]**):

```bash
docker run --rm \
  -v certbot_etc:/etc/letsencrypt \
  -v certbot_www:/var/www/certbot \
  certbot/certbot certonly --webroot -w /var/www/certbot \
  --cert-name alpha-slots \
  -d alpha1.poly-mind.net -d alpha2.poly-mind.net -d alpha3.poly-mind.net \
  --email holubvasy@gmail.com --agree-tos --no-eff-email
```

Result: `/etc/letsencrypt/live/alpha-slots/{fullchain,privkey}.pem` — the paths
`forpost-slots.conf` expects.

### 4. Enable the HTTPS block, validate, reload

Add the second mount, then reload:

```yaml
      - /opt/forpost/nginx/forpost-slots.conf:/etc/nginx/conf.d/forpost-slots.conf:ro
```

```bash
docker exec LibreChat-NGINX nginx -t
docker exec LibreChat-NGINX nginx -s reload
curl -I https://alpha1.poly-mind.net          # 503 + "Slot free" (empty, as expected)
```

Empty slots now answer with the static page. Done.

## Occupying / freeing a slot (later, via ForPost)

```bash
# occupy alpha1 with a solution (container named alpha1, :8080, shared network):
docker compose -p alpha1 -f <solution>/docker-compose.yml up -d
curl -I https://alpha1.poly-mind.net          # now proxies to the solution

# free it:
docker compose -p alpha1 -f <solution>/docker-compose.yml down
# alpha1 falls back to "Slot free" automatically — no nginx touch
```

## Cert renewal

The SAN cert renews like the others (whatever cron/timer LibreChat's certbot uses).
No slot-specific renewal — one cert, three names.
