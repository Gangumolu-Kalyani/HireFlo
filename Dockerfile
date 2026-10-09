# ============================================================
# HireFlo — Dockerfile
# ============================================================
#
# Stages
# ------
# (single stage — slim Python 3.13 is small enough)
#
# Runtime
# -------
# streamlit run app/ui/streamlit_app.py --server.address 0.0.0.0 --server.port 8501
#
# Secrets
# -------
# Do NOT bake secrets into the image.  Pass them at runtime:
#   docker run --env-file .env -p 8501:8501 hireflo:latest
#
# Security
# --------
# The application runs as a non-root user (hireflo, uid 1000).
# ============================================================

# ── Base image ────────────────────────────────────────────────────────────────
FROM python:3.13-slim

# ── Build-time metadata ───────────────────────────────────────────────────────
# Static labels baked into every build (local and CI).
# In CI, docker/metadata-action injects additional OCI labels at build time:
#   org.opencontainers.image.source    — repository URL (dynamic, not hardcoded)
#   org.opencontainers.image.revision  — exact git commit SHA
#   org.opencontainers.image.version   — version tag or branch ref
#   org.opencontainers.image.created   — ISO 8601 timestamp
LABEL org.opencontainers.image.title="HireFlo"
LABEL org.opencontainers.image.description="AI Recruitment Agent — LangGraph + Streamlit"

# ── System dependencies ───────────────────────────────────────────────────────
# curl is needed for the HEALTHCHECK only.
# --no-install-recommends keeps the layer slim.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# ── Non-root user ─────────────────────────────────────────────────────────────
RUN groupadd --gid 1000 hireflo \
    && useradd --uid 1000 --gid hireflo --no-create-home --shell /bin/bash hireflo

# ── Working directory ─────────────────────────────────────────────────────────
WORKDIR /app

# ── Python dependencies ───────────────────────────────────────────────────────
# Only the production requirements file is copied into the image.
# Development / test tools (pytest, ruff) live in requirements-dev.txt and are
# intentionally excluded from the production image.
# CI installs both: pip install -r requirements.txt -r requirements-dev.txt
COPY requirements.txt ./

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# ── Application code ──────────────────────────────────────────────────────────
COPY app/ ./app/

# ── Ownership ─────────────────────────────────────────────────────────────────
RUN chown -R hireflo:hireflo /app

# ── Switch to non-root user ───────────────────────────────────────────────────
USER hireflo

# ── Streamlit configuration ───────────────────────────────────────────────────
# Disable the Streamlit browser-open and usage-stats prompts for container use.
ENV STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
ENV STREAMLIT_SERVER_HEADLESS=true

# ── Exposed port ─────────────────────────────────────────────────────────────
EXPOSE 8501

# ── Healthcheck ───────────────────────────────────────────────────────────────
# Streamlit exposes a /_stcore/health endpoint.
# --start-period gives the app time to initialise before the first check.
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:8501/_stcore/health || exit 1

# ── Entry point ───────────────────────────────────────────────────────────────
# Shell form (not exec form) is required so that ${PORT:-8501} is expanded by
# the shell at container startup.  Render injects a $PORT environment variable;
# locally Docker uses the default of 8501.  Both cases work without any change
# to the application code.
CMD ["sh", "-c", "exec streamlit run app/ui/streamlit_app.py --server.address=0.0.0.0 --server.port=${PORT:-8501}"]
