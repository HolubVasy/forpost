# ForPost - Ubuntu-based image (matches the VPS host OS).
# Runs the FastAPI service as a non-root `forpost` user that is a member of
# the `docker` group, so it can talk to a mounted host docker socket without
# root. Base tools baked in: bash, git, gh, docker CLI + compose plugin.
FROM ubuntu:24.04

# GID of the host's docker group. Override at build time to match the host so
# the mounted /var/run/docker.sock is accessible:
#   docker compose build --build-arg DOCKER_GID=$(getent group docker | cut -d: -f3)
ARG DOCKER_GID=999

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH

# Base OS packages + repositories for Docker CLI and GitHub CLI.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        bash git curl ca-certificates gnupg lsb-release \
        sudo python3 python3-venv; \
    install -m 0755 -d /etc/apt/keyrings; \
    # Docker apt repo (CLI + compose plugin only, no daemon)
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg; \
    chmod a+r /etc/apt/keyrings/docker.gpg; \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
        > /etc/apt/sources.list.d/docker.list; \
    # GitHub CLI apt repo
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        | gpg --dearmor -o /etc/apt/keyrings/githubcli.gpg; \
    chmod a+r /etc/apt/keyrings/githubcli.gpg; \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli.gpg] \
https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        docker-ce-cli docker-compose-plugin gh; \
    rm -rf /var/lib/apt/lists/*

# Two users:
#  - forpost: runs the app + full-access commands; member of the docker group.
#  - forpost-ro: runs read-only/whitelist commands. In the forpost GROUP (to
#    read/write the shared workspace) but NOT the docker group, and - being a
#    different uid - it cannot read forpost's /proc/1/environ or owner-only
#    secrets. That is what makes read-only a real confidentiality boundary.
# The app (forpost) launches restricted commands as forpost-ro via sudo (no
# password, that runas only), keeping just the git-token vars gh/git need.
RUN set -eux; \
    if ! getent group "${DOCKER_GID}" >/dev/null; then \
        groupadd -g "${DOCKER_GID}" docker; \
    fi; \
    useradd -m -s /bin/bash forpost; \
    usermod -aG "$(getent group ${DOCKER_GID} | cut -d: -f1)" forpost; \
    useradd -m -d /home/forpost-ro -s /usr/sbin/nologin -g forpost forpost-ro; \
    printf 'Defaults env_keep += "GITHUB_TOKEN GH_TOKEN"\nforpost ALL=(forpost-ro) NOPASSWD: ALL\n' \
        > /etc/sudoers.d/forpost-ro; \
    chmod 0440 /etc/sudoers.d/forpost-ro; \
    visudo -cf /etc/sudoers.d/forpost-ro

# Python dependencies (in an isolated venv on PATH).
RUN python3 -m venv /opt/venv
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Application code + config.
WORKDIR /app
COPY app/ /app/app/
COPY whitelist.config /app/whitelist.config

# ForPost's managed workspace and logs live under the forpost home, separate
# from the baked-in application code. Backed by a volume in docker-compose.yml.
ENV WHITELIST_PATH=/app/whitelist.config \
    WORKSPACE_ROOT=/home/forpost/workspace \
    LOGS_DIR=/home/forpost/logs \
    RESTRICTED_COMMAND_USER=forpost-ro
# Permissions matter for the boundary:
#  - /home/forpost 0710: forpost-ro (group) may traverse but not list it.
#  - workspace 2770 (setgid): shared read/write between forpost and forpost-ro.
#  - logs 0700: only forpost - forpost-ro must not read the audit trail.
RUN mkdir -p /home/forpost/workspace /home/forpost/logs \
    && chown -R forpost:forpost /home/forpost /app \
    && chmod 0710 /home/forpost \
    && chmod 2770 /home/forpost/workspace \
    && chmod 0700 /home/forpost/logs

USER forpost
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# --forwarded-allow-ips=* : trust X-Forwarded-Proto/For from the reverse proxy
# (only nginx reaches this container) so generated URLs/redirects use https,
# not http. --proxy-headers is on by default but stated for clarity.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
