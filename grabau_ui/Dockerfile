# Villa Grabau Wedding Budget PWA — self-contained image for the yopflix seedbox Docker Compose stack.
# Build context = the ai-agents repo root:  docker build -f grabau_ui/Dockerfile -t grabau-fund .
FROM python:3.12-slim AS builder
WORKDIR /app
COPY grabau_ui/requirements.txt /app/grabau_ui/requirements.txt
RUN pip install --no-cache-dir --user -r /app/grabau_ui/requirements.txt

FROM python:3.12-slim
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin app
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH=/home/app/.local/bin:$PATH

COPY --from=builder /root/.local /home/app/.local

# App package + the AgentDB helper it imports.
COPY grabau_ui/ /app/grabau_ui/
COPY agents/__init__.py agents/db.py /app/agents/

# grabau.db lives here; mount a named volume/PVC at /app/data to persist it.
RUN mkdir -p /app/data && chown -R app:app /app
VOLUME ["/app/data"]

USER app
EXPOSE 8000
CMD ["python3", "-m", "uvicorn", "grabau_ui.server:app", "--host", "0.0.0.0", "--port", "8000"]
