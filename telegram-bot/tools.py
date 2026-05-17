"""Server concierge tool functions.

Each function returns a plain string for the LLM to relay to the user.
All exceptions are caught and returned as error strings.
"""
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import psutil
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))
from agents.db import AgentDB

AGENTS = ["daily-briefing", "news-briefing", "security-audit"]
DB_PATH = Path(__file__).parent.parent / "data" / "agents.db"
LOG_PATH = str(Path(__file__).parent.parent / "output" / "cron.log")
YOPFLIX_CONFIG = str(Path.home() / "git" / "yopflix" / "seedbox" / "config.yaml")
CEST_OFFSET = 2  # UTC+2


def get_agent_status() -> str:
    try:
        db = AgentDB(DB_PATH)
        lines = []
        for agent in AGENTS:
            run = db.get_last_run(agent)
            if run is None:
                lines.append(f"{agent}: never run")
            else:
                ts = run["started_at"][:16]
                status = run["status"]
                error = f" — {run['error']}" if run.get("error") else ""
                lines.append(f"{agent}: {status} at {ts}{error}")
        db.close()
        return "\n".join(lines)
    except Exception as e:
        return f"Agent status unavailable: {e}"


def get_plant_status() -> str:
    try:
        db = AgentDB(DB_PATH)
        plants = db.get_state("daily-briefing", "plants") or []
        db.close()
        if not plants:
            return "No plants tracked."
        today = date.today()
        rows = []
        for p in plants:
            last = date.fromisoformat(p["last_watered"])
            next_water = last + timedelta(days=p["frequency_days"])
            days_left = (next_water - today).days
            if days_left < 0:
                flag = f" ⚠ OVERDUE by {-days_left}d"
            elif days_left == 0:
                flag = " (today!)"
            else:
                flag = ""
            rows.append((next_water, f"{p['name']} ({p['location']}): next {next_water}{flag}"))
        rows.sort()
        return "\n".join(r[1] for r in rows)
    except Exception as e:
        return f"Plant status unavailable: {e}"


def get_yopflix_status() -> str:
    parts = []

    # Enabled services from config
    try:
        with open(YOPFLIX_CONFIG) as f:
            cfg = yaml.safe_load(f)
        services = cfg.get("services", [])
        enabled = [s["name"] for s in services if s.get("enabled")]
        disabled = [s["name"] for s in services if not s.get("enabled")]
        parts.append(f"Enabled ({len(enabled)}): {', '.join(enabled) or 'none'}")
        if disabled:
            parts.append(f"Disabled: {', '.join(disabled)}")
    except FileNotFoundError:
        parts.append("Config not found — yopflix config unavailable")
    except Exception as e:
        parts.append(f"Config error: {e}")

    # Docker containers
    try:
        res = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}\t{{.Status}}"],
            capture_output=True, text=True, timeout=10,
        )
        lines = [l for l in res.stdout.strip().splitlines() if l]
        if lines:
            parts.append("Containers:\n" + "\n".join(f"  {l}" for l in lines))
        else:
            parts.append("No containers running")
    except FileNotFoundError:
        parts.append("Docker unavailable (not installed or not in PATH)")
    except Exception as e:
        parts.append(f"Docker error: {e}")

    # Disk usage
    try:
        disk_path = "/data/media" if Path("/data/media").exists() else "/"
        res = subprocess.run(
            ["df", "-h", disk_path],
            capture_output=True, text=True, timeout=10,
        )
        lines = res.stdout.strip().splitlines()
        if len(lines) >= 2:
            parts.append(f"Disk ({disk_path}): {lines[1]}")
    except Exception as e:
        parts.append(f"Disk info error: {e}")

    return "\n".join(parts)


def get_system_health() -> str:
    try:
        cpu = psutil.cpu_percent(interval=0.5)
        mem = psutil.virtual_memory()
        boot = psutil.boot_time()
        uptime_secs = time.time() - boot
        days = int(uptime_secs // 86400)
        hours = int((uptime_secs % 86400) // 3600)
        used_gb = mem.used / 1024 ** 3
        total_gb = mem.total / 1024 ** 3
        return (
            f"CPU: {cpu:.1f}%\n"
            f"RAM: {mem.percent:.1f}% ({used_gb:.1f}/{total_gb:.1f} GB)\n"
            f"Uptime: {days}d {hours}h"
        )
    except Exception as e:
        return f"System health unavailable: {e}"


def _cron_to_human(minute: str, hour: str) -> str:
    """Convert cron minute/hour fields to a human-readable time string (CEST)."""
    try:
        utc_h = int(hour)
        utc_m = int(minute)
        cest_h = (utc_h + CEST_OFFSET) % 24
        return f"daily at {cest_h:02d}:{utc_m:02d} CEST"
    except ValueError:
        return f"{minute} {hour} * * * (cron)"


def get_cron_schedule() -> str:
    try:
        res = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=5)
        if res.returncode != 0 or "no crontab" in res.stdout.lower():
            return "No crontab schedule found."
        lines = []
        in_managed = False
        for line in res.stdout.splitlines():
            if "ai-agents managed" in line:
                in_managed = True
                continue
            if "end ai-agents" in line:
                in_managed = False
                continue
            if not in_managed or not line.strip() or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 6:
                continue
            minute, hour = parts[0], parts[1]
            cmd = " ".join(parts[5:])
            agent = None
            for a in AGENTS:
                if a in cmd:
                    agent = a
                    break
            if agent:
                lines.append(f"{agent}: {_cron_to_human(minute, hour)}")
        return "\n".join(lines) if lines else "No ai-agents cron entries found."
    except Exception as e:
        return f"Cron schedule unavailable: {e}"


def get_agent_logs(agent_name: str = "") -> str:
    try:
        with open(LOG_PATH) as f:
            all_lines = f.read().splitlines()
        if agent_name:
            filtered = [l for l in all_lines if agent_name in l]
        else:
            filtered = all_lines
        tail = filtered[-20:]
        return "\n".join(tail) if tail else f"No log entries{' for ' + agent_name if agent_name else ''}."
    except FileNotFoundError:
        return "Log file not found."
    except Exception as e:
        return f"Logs unavailable: {e}"
