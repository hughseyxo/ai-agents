# Wedding Budget PWA — self-contained image for the yopflix Traefik stack.
# Build context = the ai-agents repo root:  docker build -f wedding_ui/Dockerfile -t wedding-fund .
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

COPY wedding_ui/requirements.txt /app/wedding_ui/requirements.txt
RUN pip install --no-cache-dir -r /app/wedding_ui/requirements.txt

# App package + the AgentDB helper it imports.
COPY wedding_ui/ /app/wedding_ui/
COPY agents/__init__.py agents/db.py /app/agents/

# wedding.db lives here; mount a named volume at /app/data to persist it.
RUN mkdir -p /app/data
VOLUME ["/app/data"]

EXPOSE 8000
CMD ["uvicorn", "wedding_ui.server:app", "--host", "0.0.0.0", "--port", "8000"]
