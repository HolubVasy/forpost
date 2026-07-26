# Sandboxed autonomous Claude Code agent (POC).
#
# ForPost (full-access) drives it:  docker exec claude-runner task "<goal>"
# It clones a repo into /work, edits, tests, commits and PUSHES a branch - then
# ForPost deploys the solution from git as a SEPARATE container.
#
# Isolation: NO docker socket, NO host mounts -> the blast radius is this
# container. Runs as a non-root user (required: --permission-mode
# bypassPermissions refuses to run as root). Auth is a Claude *subscription*
# OAuth token (CLAUDE_CODE_OAUTH_TOKEN), not an Anthropic API key.
FROM node:22-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates jq \
 && rm -rf /var/lib/apt/lists/* \
 && npm install -g @anthropic-ai/claude-code

# Non-root user in its own home. bypassPermissions will not run as root.
RUN useradd -m -s /bin/bash runner
COPY task /usr/local/bin/task
RUN chmod +x /usr/local/bin/task

USER runner
WORKDIR /work
RUN git config --global user.name "claude-runner" \
 && git config --global user.email "runner@localhost"

# Idle; ForPost execs `task "<goal>"` into it per request.
CMD ["sleep", "infinity"]
