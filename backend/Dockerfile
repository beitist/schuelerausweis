# ============================================================================
# Hagen-ID Backend Dockerfile
# ============================================================================
# Multi-Stage Build für kleineres Image
# Produktion: ~150MB statt ~800MB

# Basis exakt gepinnt (Python-Patch + Debian-Codename), in beiden Stufen gleich
# und gleich wie in der CI. Jeder Versionswechsel ist ein Commit. Ein Pin friert
# ein, sobald der nächste Patch erscheint (python:3.11.11-slim blieb so bis 11/2026
# auf Debian 12.10); den Bump mahnt der wöchentliche Trivy-Bericht an.

# ----- Stage 1: Builder -----
FROM python:3.11.18-slim-trixie as builder

WORKDIR /app

# System-Dependencies für psycopg2 (Postgres-Treiber)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Python Dependencies in virtuelles Environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


# ----- Stage 2: Production -----
FROM python:3.11.18-slim-trixie

WORKDIR /app

# Nur Runtime-Libs für Postgres
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Virtual Environment vom Builder kopieren
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Anwendungscode kopieren
COPY . .

# Nicht als root laufen (Security Best Practice)
RUN useradd --create-home --shell /bin/bash appuser
USER appuser

# Port exponieren
EXPOSE 8000

# Health-Check für Container-Orchestrierung
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Startbefehl mit Uvicorn (Produktion: mehr Worker)
# In Coolify: Kann über Environment überschrieben werden
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]