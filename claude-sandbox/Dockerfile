# Sandboxed autonomous Claude Code agent (POC).
#
# ForPost (full-access) drives it:  docker exec claude-builder task "<goal>"
# It clones a repo into /work, edits, tests, commits and PUSHES a branch - then
# ForPost deploys the solution from git as a SEPARATE container.
#
# Isolation: NO docker socket, NO host mounts -> the blast radius is this
# container. Runs as a non-root user (required: --permission-mode
# bypassPermissions refuses to run as root). Auth is a Claude *subscription*
# OAuth token (CLAUDE_CODE_OAUTH_TOKEN), not an Anthropic API key.
FROM node:22-slim

# git + gh (so the agent can clone AND create repos / open PRs itself) + jq.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates jq curl gnupg \
 && install -m 0755 -d /etc/apt/keyrings \
 && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      | gpg --dearmor -o /etc/apt/keyrings/githubcli.gpg \
 && chmod go+r /etc/apt/keyrings/githubcli.gpg \
 && echo "deb [signed-by=/etc/apt/keyrings/githubcli.gpg] https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list \
 && apt-get update && apt-get install -y --no-install-recommends gh \
 && rm -rf /var/lib/apt/lists/* \
 && npm install -g @anthropic-ai/claude-code

# Non-root user in its own home. bypassPermissions will not run as root.
RUN useradd -m -s /bin/bash builder
COPY task clean /usr/local/bin/
RUN chmod +x /usr/local/bin/task /usr/local/bin/clean

USER builder
WORKDIR /work
RUN git config --global user.name "claude-builder" \
 && git config --global user.email "builder@localhost"

# Idle; ForPost execs `task "<goal>"` into it per request.
CMD ["sleep", "infinity"]
