# Wedding Budget PWA — self-contained image for the k3s cluster.
# Build context = the ai-agents repo root:  docker build -f wedding_ui/Dockerfile -t wedding-fund .
FROM python:3.12-slim AS builder
WORKDIR /app
COPY wedding_ui/requirements.txt /app/wedding_ui/requirements.txt
RUN pip install --no-cache-dir --user -r /app/wedding_ui/requirements.txt

FROM python:3.12-slim
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin app
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH=/home/app/.local/bin:$PATH

COPY --from=builder /root/.local /home/app/.local

# App package + the AgentDB helper it imports.
COPY wedding_ui/ /app/wedding_ui/
COPY agents/__init__.py agents/db.py /app/agents/

# wedding.db lives here; mount a named volume/PVC at /app/data to persist it.
RUN mkdir -p /app/data && chown -R app:app /app
VOLUME ["/app/data"]

USER app
EXPOSE 8000
CMD ["python3", "-m", "uvicorn", "wedding_ui.server:app", "--host", "0.0.0.0", "--port", "8000"]
