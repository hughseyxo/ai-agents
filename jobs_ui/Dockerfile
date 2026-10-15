# Job Hunt PWA — self-contained image for the yopflix seedbox Docker Compose stack.
# Build context = the ai-agents repo root:  docker build -f jobs_ui/Dockerfile -t jobs-ui .
# No LLM or Google credentials in here: the job-hunt agent runs on the host and
# writes data/jobs/jobs.db, which is bind-mounted at /app/data/jobs.
FROM python:3.12-slim AS builder
WORKDIR /app
COPY jobs_ui/requirements.txt /app/jobs_ui/requirements.txt
RUN pip install --no-cache-dir --user -r /app/jobs_ui/requirements.txt

FROM python:3.12-slim
# Match the host user that owns data/jobs/ so SQLite (WAL) can write through the bind mount.
ARG APP_UID=1001
RUN useradd --uid ${APP_UID} --create-home --shell /usr/sbin/nologin app
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH=/home/app/.local/bin:$PATH

COPY --from=builder /root/.local /home/app/.local

COPY jobs_ui/ /app/jobs_ui/
COPY agents/__init__.py agents/job_store.py /app/agents/

RUN mkdir -p /app/data/jobs && chown -R app:app /app
USER app
EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz', timeout=4)"
CMD ["python3", "-m", "uvicorn", "jobs_ui.server:app", "--host", "0.0.0.0", "--port", "8000"]
