"""Shared Telegram sender for agents.

Credentials come from the environment (injected by run-agent.sh from the
repo-root .env): CONCIERGE_BOT_TOKEN is the bot token, TELEGRAM_USER_ID is
the chat id to send to.
"""

import os

import requests


def telegram_creds() -> tuple[str, str]:
    return (
        os.environ.get("CONCIERGE_BOT_TOKEN", ""),
        os.environ.get("TELEGRAM_USER_ID", ""),
    )


def send_telegram(text: str, parse_mode: str | None = "Markdown") -> bool:
    """Send a Telegram message. Returns False (never raises) on missing creds
    or any request failure, so a Telegram outage never breaks an agent run.

    parse_mode defaults to "Markdown" for callers building their own trusted
    message text. Callers embedding third-party free text (e.g. job titles)
    should pass parse_mode=None — Telegram's Markdown parser 400s on
    unbalanced `_`/`*`/`(`/`)`, which untrusted text isn't guaranteed to avoid.
    """
    token, chat_id = telegram_creds()
    if not token or not chat_id:
        print("[telegram_client] Missing Telegram creds; skipping push")
        return False
    payload = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json=payload,
            timeout=10,
        )
        if resp.status_code != 200:
            print(f"[telegram_client] Telegram send failed: HTTP {resp.status_code} {resp.text[:200]}")
        return resp.status_code == 200
    except requests.RequestException as e:
        print(f"[telegram_client] Telegram send failed: {e}")
        return False
