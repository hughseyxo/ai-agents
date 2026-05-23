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

AGENTS = ["daily-briefing", "news-briefing", "security-audit", "travel-agent"]
DB_PATH = Path(__file__).parent.parent / "data" / "agents.db"
LOG_PATH = str(Path(__file__).parent.parent / "output" / "cron.log")
YOPFLIX_CONFIG = str(Path.home() / "git" / "yopflix" / "seedbox" / "config.yaml")
REPO_ROOT = Path(__file__).parent.parent
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


def run_travel_agent(
    destination: str,
    checkin: str,
    checkout: str,
    mode: str = "search",
    origin: str = "",
    flights: str = "",
    hotel: str = "",
) -> str:
    """Launch the travel agent as a background subprocess."""
    try:
        cmd = [
            "python3", "-m", "agents", "travel-agent",
            "--mode", mode,
            "--destination", destination,
            "--checkin", checkin,
            "--checkout", checkout,
        ]
        if origin:
            cmd += ["--origin", origin]
        if flights:
            cmd += ["--flights", flights]
        if hotel:
            cmd += ["--hotel", hotel]

        log_path = REPO_ROOT / "output" / "travel-agent.log"
        with open(log_path, "a") as log:
            subprocess.Popen(cmd, cwd=str(REPO_ROOT), stdout=log, stderr=log)

        dest_slug = destination.lower().replace(" ", "-")
        report_name = f"travel-{dest_slug}-{checkin}.html"

        if mode == "plan":
            return (
                f"Planning your {destination} trip ({checkin}–{checkout}) in the background. "
                f"Report will be at output/{report_name}. "
                "Ask me 'is the travel report ready?' in a minute or two."
            )
        else:
            origin_str = f"{origin} → " if origin else ""
            return (
                f"Searching flights and hotels for {origin_str}{destination} ({checkin}–{checkout}) in the background. "
                f"Report will be at output/{report_name}. "
                "Ask me 'is the travel report ready?' in a few minutes."
            )
    except Exception as e:
        return f"Failed to start travel agent: {e}"


VIDQUEUE_PY = REPO_ROOT / "skills" / "vidqueue" / "vidqueue.py"
VIDQUEUE_PYTHON = REPO_ROOT / "skills" / "vidqueue" / ".venv" / "bin" / "python"


def queue_tiktok(url: str) -> str:
    """Run vidqueue.py against a TikTok URL and return a plain-text summary."""
    python = str(VIDQUEUE_PYTHON) if VIDQUEUE_PYTHON.exists() else "python3"
    try:
        result = subprocess.run(
            [python, str(VIDQUEUE_PY), url],
            capture_output=True, text=True, timeout=300,
            cwd=str(REPO_ROOT),
        )
    except subprocess.TimeoutExpired:
        return "Timed out after 5 minutes — the TikTok may have needed a full video download. Try again."
    except FileNotFoundError:
        return "vidqueue not installed. Run: cd skills/vidqueue && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"

    if result.returncode != 0:
        combined = f"{result.stdout}\n{result.stderr}".strip()
        import re as _re
        m = _re.search(r'^ERROR:\s*(.*)$', combined, _re.MULTILINE)
        return f"Error: {m.group(1).strip() if m else combined[-400:]}"

    added, skipped, unresolved, playlist_url = [], [], [], None
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("ADDED:"):
            parts = line.split(":", 2)
            if len(parts) == 3:
                added.append(f"• {parts[2]} → youtu.be/{parts[1]}")
        elif line.startswith("SKIPPED:"):
            skipped.append(line)
        elif line.startswith("UNRESOLVED:"):
            unresolved.append(line.split(":", 1)[1] if ":" in line else line)
        elif line.startswith("PLAYLIST:"):
            parts = line.split(":", 2)
            if len(parts) == 3:
                playlist_url = parts[2]

    if not added and not unresolved:
        return "No YouTube recommendations found in this TikTok."

    out = []
    if added:
        out.append(f"Added {len(added)} video{'s' if len(added) != 1 else ''}:\n" + "\n".join(added))
    if skipped:
        out.append(f"Already in playlist: {len(skipped)}")
    if unresolved:
        out.append(f"Couldn't resolve: {', '.join(unresolved)}")
    if playlist_url:
        out.append(f"Playlist: {playlist_url}")
    return "\n".join(out)


def get_travel_report() -> str:
    """Check whether the latest travel report is ready and return its name."""
    try:
        output_dir = REPO_ROOT / "output"
        reports = sorted(
            output_dir.glob("travel-*.html"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not reports:
            return "No travel reports found yet. The agent may still be running."
        latest = reports[0]
        modified = datetime.fromtimestamp(latest.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        size_kb = latest.stat().st_size // 1024
        return f"Latest travel report: {latest.name} — ready ({size_kb} KB, saved {modified})."
    except Exception as e:
        return f"Travel report check unavailable: {e}"
