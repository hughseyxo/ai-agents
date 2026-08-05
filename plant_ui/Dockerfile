# ai-agents-plant-ui — FloraPulse PWA (chat + Opus vision diagnostics).
# Runs the claude CLI directly (chat_backend.py, claude_backend.py), same
# runtime shape as Dockerfile.runner but a separate image: different
# entrypoint (a long-running web service, not a one-shot agent run) and a
# narrower dependency set (no python-telegram-bot).
# Build context = the ai-agents repo root:
#   docker build -f plant_ui/Dockerfile -t ai-agents-plant-ui .
FROM node:20-slim
RUN npm install -g @anthropic-ai/claude-code@2.1.92

RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-pip curl && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 1001 cian && \
    useradd --uid 1001 --gid 1001 --create-home --shell /bin/bash cian

WORKDIR /home/cian/git/ai-agents
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

COPY agents/requirements.txt /home/cian/git/ai-agents/agents/requirements.txt
COPY plant_ui/requirements.txt /home/cian/git/ai-agents/plant_ui/requirements.txt
RUN pip install --no-cache-dir --break-system-packages \
      -r agents/requirements.txt -r plant_ui/requirements.txt

COPY agents/ /home/cian/git/ai-agents/agents/
COPY mcp-servers/ /home/cian/git/ai-agents/mcp-servers/
COPY plant_ui/ /home/cian/git/ai-agents/plant_ui/
# Only the three telegram-bot/ files plant-ui's dependency chain actually
# needs (see this plan's Global Constraints) — not the rest of telegram-bot/.
COPY telegram-bot/claude_backend.py telegram-bot/tool_specs.py telegram-bot/tools.py \
     /home/cian/git/ai-agents/telegram-bot/

RUN mkdir -p /home/cian/git/ai-agents/data /home/cian/git/ai-agents/output && \
    chown -R cian:cian /home/cian/git/ai-agents /home/cian

USER cian
EXPOSE 8765
CMD ["python3", "-m", "uvicorn", "plant_ui.server:app", "--host", "0.0.0.0", "--port", "8765"]
