# Hardened workload image (CLAUDE.md invariant #3): runs untrusted/model-generated code.
# Non-root, reproducible (uv sync --frozen), no model libs. Runtime hardening (read_only rootfs,
# cap_drop ALL, no-new-privileges, tmpfs sandbox, no model route) is applied in docker-compose.yml.
FROM python:3.12-slim

# uv binary (HANDOFF §4.5: uv in Dockerfiles, never bare pip).
COPY --from=ghcr.io/astral-sh/uv:0.11.17 /uv /usr/local/bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Resolve deps from the committed lock — reproducible, no dev deps.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY exec_service ./exec_service

# Non-root runtime user; /work is the sandbox (mounted as a tmpfs by compose at runtime).
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /work && chown appuser:appuser /work
USER 10001:10001

EXPOSE 8800
CMD ["uvicorn", "exec_service.server:app", "--host", "0.0.0.0", "--port", "8800"]
