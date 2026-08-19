# The judging sandbox. The point is what it does NOT contain: no
# ~/.claude/settings.json, no user CLAUDE.md, no skills, no MCP registration,
# no /etc/claude-code/managed-settings.json — and, because only one contract's
# workspace is mounted, no dataset.csv and no other contract.
#
# Managed policy is the reason this matters most: it is read from
# /etc/claude-code/, so no environment sweep can reach it. A fresh Ubuntu has
# no such file.
#
# Pinned by digest, not tag — `ubuntu:24.04` is a moving target.
FROM ubuntu@sha256:d78ab76437b1afc5f01e223d6bf0172763f404bb166441328845adbef44518cb

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        python3.12 python3.12-venv ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN python3.12 -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

# The committed pins. Only claude-agent-sdk is needed to judge, but installing
# the whole file keeps requirements.txt honest — and on Linux/cp312 all four
# resolve, including matplotlib==3.8.4, which has no cp313 Windows wheel.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# The SDK ships a CLI in its own wheel and prefers it to anything on PATH, so
# pinning the SDK pins the CLI. Assert that here, at BUILD time — the claim
# rests on it, and it is the kind of assumption that has already cost this
# project a run.
RUN CLI="$(find /opt/venv -name claude -type f -path '*_bundled*' | head -1)" \
 && test -n "$CLI" && "$CLI" --version | grep -q '^2\.1\.233 '

RUN useradd --create-home --uid 1001 judge \
 && mkdir -p /home/judge/.claude \
 && chown -R judge:judge /home/judge
USER judge
ENV HOME=/home/judge

# ~/.claude is created above, owned by judge, because the credential is
# bind-mounted in as a single file at ~/.claude/.credentials.json. Without the
# directory, Docker would create it root-owned and the CLI could not write its
# session store beside it.

# The harness, baked in rather than mounted, and NOT under /work — so the
# confinement hook denies the agent access to it like any other outside path.
COPY --chown=judge:judge docker/judge_one.py \
                         src/experiments/isolation.py \
                         src/experiments/predictions.py \
                         /opt/harness/

WORKDIR /work
ENTRYPOINT ["python", "/opt/harness/judge_one.py"]
