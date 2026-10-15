# Way of Kings companion site — self-contained image for the yopflix seedbox stack.
# Build context = the ai-agents repo root:  docker build -f wok_tracker/Dockerfile -t wok-tracker .
FROM python:3.12-slim AS builder
WORKDIR /app
COPY wok_tracker/requirements.txt /app/wok_tracker/requirements.txt
RUN pip install --no-cache-dir --user -r /app/wok_tracker/requirements.txt

FROM python:3.12-slim
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin app
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH=/home/app/.local/bin:$PATH

COPY --from=builder /root/.local /home/app/.local

COPY wok_tracker/ /app/wok_tracker/

RUN chown -R app:app /app
USER app
EXPOSE 8000
CMD ["python3", "-m", "uvicorn", "wok_tracker.server:app", "--host", "0.0.0.0", "--port", "8000"]
