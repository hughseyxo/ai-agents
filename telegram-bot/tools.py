"""Server concierge tool functions.

Each function returns a plain string for the LLM to relay to the user.
All exceptions are caught and returned as error strings.
"""
import ipaddress
import re
import socket
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import psutil
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))
from agents.db import AgentDB
from agents.plant_weather import weather_adjusted_frequency, MIN_FREQUENCY, MAX_FREQUENCY
from agents.weather import fetch_weather
from agents.plant_profiles import append_frequency_history, write_profile_atomic, upsert_frontmatter, parse_frontmatter, safe_profile_path

AGENTS = ["daily-briefing", "news-briefing", "security-audit", "travel-agent", "librarian", "plant-agent", "agent-health"]
DB_PATH = Path(__file__).parent.parent / "data" / "agents.db"
LOG_PATH = str(Path(__file__).parent.parent / "output" / "cron.log")
YOPFLIX_CONFIG = str(Path.home() / "git" / "yopflix" / "seedbox" / "config.yaml")
REPO_ROOT = Path(__file__).parent.parent
CEST_OFFSET = 2  # UTC+2
SUNLIGHT_VALUES = ("full sun", "partial shade", "shade")
SENSITIVITY_VALUES = ("high", "medium", "low")


def _validate_http_url(url: str) -> str | None:
    """Return None if the URL is a safe public http(s) URL, else an error string.

    Blocks non-http(s) schemes, argv flag-smuggling (a leading '-'), and SSRF to
    private / loopback / link-local hosts (defence in depth — the recipe scraper
    does its own check too)."""
    url = url.strip()
    if not url or url.startswith("-"):
        return "Invalid URL."
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return "Only http(s) URLs are supported."
    host = parsed.hostname
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return "Could not resolve URL host."
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return "Refusing to fetch a private/internal address."
    return None


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
        cache = {r["plant_name"]: r for r in db.get_plant_weather_cache()}
        db.close()
        if not plants:
            return "No plants tracked."
        today = date.today()
        rows = []
        for p in plants:
            last = date.fromisoformat(p["last_watered"])
            base_date = last + timedelta(days=p["frequency_days"])
            cached = cache.get(p["name"])
            if cached:
                next_water = date.fromisoformat(cached["adjusted_date"])
                reason = f" [{cached['adjustment_reason']}]" if cached.get("adjustment_reason") else ""
            else:
                next_water = base_date
                reason = ""
            days_left = (next_water - today).days
            if days_left < 0:
                flag = f" ⚠ OVERDUE by {-days_left}d"
            elif days_left == 0:
                flag = " (today!)"
            else:
                flag = ""
            assessed = ""
            if p.get("last_assessment"):
                assessed = f", assessed {p['last_assessment']['date']}"
            sun = p.get("sunlight", "")
            loc_str = f"{p['location']}, {sun}" if sun else p['location']
            rows.append((next_water, f"{p['name']} ({loc_str}): next {next_water}{flag}{reason}{assessed}"))
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
        if res.returncode != 0:
            parts.append(f"Docker error: {res.stderr.strip()[:200] or 'docker ps failed'}")
        else:
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
        # Validate every value that becomes argv so a leading '-' can't smuggle a
        # flag into the subprocess, and dates/mode are well-formed.
        if mode not in ("search", "plan"):
            return "mode must be 'search' or 'plan'."
        for label, value in (("checkin", checkin), ("checkout", checkout)):
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", value):
                return f"{label} must be an ISO date (YYYY-MM-DD)."
        for label, value in (("destination", destination), ("origin", origin),
                             ("flights", flights), ("hotel", hotel)):
            if value and value.lstrip().startswith("-"):
                return f"Invalid {label}."
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


def _find_plant(name: str, plants: list[dict]) -> dict | None:
    """Find a plant by exact name match (case-insensitive) or substring match."""
    name_lower = name.lower().strip()
    return (
        next((p for p in plants if p["name"].lower() == name_lower), None)
        or next((p for p in plants if name_lower in p["name"].lower()), None)
    )


def research_plant_watering(plant_name: str) -> str:
    prompt = (
        f"What is the recommended watering frequency in days for a {plant_name} houseplant? "
        "Consider it is kept indoors. Reply with only a single integer (number of days between waterings). "
        "For example: 7"
    )
    try:
        res = subprocess.run(
            ["agy", "-y", "-o", "text"],
            input=prompt,
            capture_output=True, text=True, timeout=30,
            cwd=str(REPO_ROOT),
        )
        if res.returncode == 0 and res.stdout.strip():
            import re
            match = re.search(r"\d+", res.stdout.strip())
            if match:
                return match.group()
        return f"Could not determine watering frequency: {res.stderr[:100]}"
    except Exception as e:
        return f"Research failed: {e}"


def research_plant_sunlight(plant_name: str) -> str:
    prompt = (
        f"What are the sunlight requirements for a {plant_name} plant? "
        "Reply with exactly one of: 'full sun', 'partial shade', or 'shade'. "
        "Reply with only that phrase and nothing else."
    )
    try:
        res = subprocess.run(
            ["agy", "-y", "-o", "text"],
            input=prompt,
            capture_output=True, text=True, timeout=30,
            cwd=str(REPO_ROOT),
        )
        if res.returncode == 0 and res.stdout.strip():
            answer = res.stdout.strip().lower()
            for val in SUNLIGHT_VALUES:
                if val in answer:
                    return val
        return f"Could not determine sunlight requirements: {res.stderr[:100]}"
    except Exception as e:
        return f"Research failed: {e}"


def research_plant_water_sensitivity(plant_name: str) -> str:
    prompt = (
        f"What is the water sensitivity of a {plant_name} plant? "
        "High sensitivity means it is very prone to overwatering (cacti, succulents, snake plant, ZZ plant). "
        "Low sensitivity means it prefers consistently moist soil (ferns, peace lily, carnivorous plants). "
        "Medium covers most common houseplants (pothos, monstera, philodendron). "
        "Reply with exactly one of: 'high', 'medium', or 'low'. "
        "Reply with only that word and nothing else."
    )
    try:
        res = subprocess.run(
            ["agy", "-y", "-o", "text"],
            input=prompt,
            capture_output=True, text=True, timeout=30,
            cwd=str(REPO_ROOT),
        )
        if res.returncode == 0 and res.stdout.strip():
            answer = res.stdout.strip().lower()
            for val in SENSITIVITY_VALUES:
                if val in answer:
                    return val
        return f"Could not determine water sensitivity: {res.stderr[:100]}"
    except Exception as e:
        return f"Research failed: {e}"


def add_plant(name: str, frequency_days: int, location: str = "indoor", sunlight: str = "") -> str:
    try:
        db = AgentDB(DB_PATH)
        plants = db.get_state("daily-briefing", "plants") or []
        name_lower = name.lower().strip()
        if any(p["name"].lower() == name_lower for p in plants):
            db.close()
            return f"A plant named '{name}' already exists."

        sensitivity = research_plant_water_sensitivity(name)
        if sensitivity not in SENSITIVITY_VALUES:
            sensitivity = "medium"

        plants.append({
            "name": name,
            "frequency_days": frequency_days,
            "baseline_frequency_days": frequency_days,
            "last_watered": date.today().isoformat(),
            "location": location,
            "sunlight": sunlight,
            "water_sensitivity": sensitivity,
        })
        db.set_state("daily-briefing", "plants", plants)
        db.close()
        sun_str = f", {sunlight}" if sunlight else ""
        return (
            f"{name} added ({location}{sun_str}, water every {frequency_days} days, "
            f"sensitivity: {sensitivity}). Last watered set to today."
        )
    except Exception as e:
        return f"Failed to add plant: {e}"


def update_plant(plant_name: str, location: str = "", frequency_days: int = 0, sunlight: str = "") -> str:
    try:
        db = AgentDB(DB_PATH)
        plants = db.get_state("daily-briefing", "plants") or []
        match = _find_plant(plant_name, plants)
        if not match:
            names = ", ".join(p["name"] for p in plants)
            db.close()
            return f"No plant named '{plant_name}' found. Known plants: {names or 'none'}"
        changes = []
        if location in ("indoor", "outdoor"):
            match["location"] = location
            changes.append(f"location → {location}")
        if frequency_days > 0:
            # Set the baseline too, else the hourly weather recompute reverts it.
            match["baseline_frequency_days"] = frequency_days
            match["frequency_days"] = frequency_days
            changes.append(f"frequency → every {frequency_days} days")
        if sunlight in SUNLIGHT_VALUES:
            match["sunlight"] = sunlight
            changes.append(f"sunlight → {sunlight}")
        if not changes:
            db.close()
            return "Nothing to update — specify location ('indoor'/'outdoor'), frequency_days, or sunlight."
        db.set_state("daily-briefing", "plants", plants)
        db.close()
        return f"{match['name']} updated: {', '.join(changes)}."
    except Exception as e:
        return f"Failed to update plant: {e}"


def water_plant(plant_name: str) -> str:
    try:
        db = AgentDB(DB_PATH)
        plants = db.get_state("daily-briefing", "plants") or []
        match = _find_plant(plant_name, plants)
        if not match:
            names = ", ".join(p["name"] for p in plants)
            db.close()
            return f"No plant named '{plant_name}' found. Known plants: {names or 'none'}"
        match["last_watered"] = date.today().isoformat()
        db.set_state("daily-briefing", "plants", plants)
        db.close()
        return f"{match['name']} marked as watered today ({match['last_watered']})."
    except Exception as e:
        return f"Failed to update plant: {e}"


def water_plants(location: str) -> str:
    try:
        db = AgentDB(DB_PATH)
        plants = db.get_state("daily-briefing", "plants") or []
        targets = [p for p in plants if p.get("location") == location]
        if not targets:
            db.close()
            return f"No {location} plants found."
        today = date.today().isoformat()
        for p in targets:
            p["last_watered"] = today
        db.set_state("daily-briefing", "plants", plants)
        db.close()
        names = ", ".join(p["name"] for p in targets)
        return f"Marked {len(targets)} {location} plant{'s' if len(targets) != 1 else ''} as watered today: {names}."
    except Exception as e:
        return f"Failed to update plants: {e}"


def remove_plant(plant_name: str) -> str:
    try:
        db = AgentDB(DB_PATH)
        plants = db.get_state("daily-briefing", "plants") or []
        match = _find_plant(plant_name, plants)
        if not match:
            names = ", ".join(p["name"] for p in plants)
            db.close()
            return f"No plant named '{plant_name}' found. Known plants: {names or 'none'}"
        plants = [p for p in plants if p is not match]
        db.set_state("daily-briefing", "plants", plants)
        db.close()
        return f"{match['name']} removed from plant tracker."
    except Exception as e:
        return f"Failed to remove plant: {e}"


def get_all_plants() -> list[dict]:
    try:
        db = AgentDB(DB_PATH)
        plants = db.get_state("daily-briefing", "plants") or []
        db.close()
        return plants
    except Exception:
        return []


def get_plant(plant_name: str) -> dict | None:
    plants = get_all_plants()
    return _find_plant(plant_name, plants)


def save_plant_assessment(plant_name: str, summary: str) -> str:
    try:
        db = AgentDB(DB_PATH)
        plants = db.get_state("daily-briefing", "plants") or []
        match = _find_plant(plant_name, plants)
        if not match:
            db.close()
            return f"No plant named '{plant_name}' found — assessment not saved."
        today = date.today().isoformat()
        match["last_assessment"] = {"date": today, "summary": summary}
        db.set_state("daily-briefing", "plants", plants)
        db.close()
        # Refresh latest_health in frontmatter projection (merge, preserve other fields)
        from agents.plant_profiles import profile_path as _pp
        path = _pp(match["name"])
        if path.exists():
            existing_meta, _ = parse_frontmatter(path.read_text())
            existing_meta["latest_health"] = {"date": today, "summary": summary}
            upsert_frontmatter(match["name"], existing_meta)
        return f"{match['name']} assessment saved."
    except Exception as e:
        return f"Failed to save assessment: {e}"


def note_plant_observation(name: str, notes: str) -> str:
    try:
        profile_path = safe_profile_path(name)
    except ValueError:
        return f"Invalid plant name: {name!r}"
    if not profile_path.exists():
        return f"No profile doc found for {name}"
    content = profile_path.read_text()
    write_profile_atomic(profile_path, f"{content}\n{notes}")
    return f"Observation recorded for {name}"


MEALSAVE_PY = REPO_ROOT / "skills" / "mealsave" / "mealsave.py"
MEALSAVE_PYTHON = REPO_ROOT / "skills" / "mealsave" / ".venv" / "bin" / "python"


def save_recipe(url: str) -> str:
    err = _validate_http_url(url)
    if err:
        return err
    python = str(MEALSAVE_PYTHON) if MEALSAVE_PYTHON.exists() else "python3"
    try:
        result = subprocess.run(
            [python, str(MEALSAVE_PY), url],
            capture_output=True, text=True, timeout=120,
            cwd=str(REPO_ROOT),
        )
    except subprocess.TimeoutExpired:
        return "Timed out saving recipe. Try again."
    except FileNotFoundError:
        return "mealsave not installed."

    output = result.stdout.strip()
    if result.returncode != 0 or not output:
        import re as _re
        err = result.stderr.strip()
        m = _re.search(r'^ERROR:\s*(.*)$', err or output, _re.MULTILINE)
        return f"Error: {m.group(1).strip() if m else (err or output)[-400:]}"
    return f"Recipe saved: {output}"




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


def set_plant_frequency(plant_name: str, frequency_days: int, reason: str = "") -> str:
    """Set a plant's BASELINE watering frequency (1-30 days). Weather is folded
    into the effective schedule automatically. Logs the change to the profile."""
    try:
        db = AgentDB(DB_PATH)
        plants = db.get_state("daily-briefing", "plants") or []
        match = _find_plant(plant_name, plants)
        if not match:
            names = ", ".join(p["name"] for p in plants)
            db.close()
            return f"No plant named '{plant_name}' found. Known plants: {names or 'none'}"
        target = max(MIN_FREQUENCY, min(MAX_FREQUENCY, int(frequency_days)))
        old = match.get("baseline_frequency_days", match["frequency_days"])
        match["baseline_frequency_days"] = target
        match["frequency_days"], _ = weather_adjusted_frequency(match, fetch_weather())
        db.set_state("daily-briefing", "plants", plants)
        db.close()
        if old != target:
            append_frequency_history(match["name"], old, target, f"bot: {reason}".rstrip(": ").strip())
        eff = match["frequency_days"]
        suffix = f" (effective {eff}d after weather)" if eff != target else ""
        return f"{match['name']} base frequency set to {target} days{suffix}."
    except Exception as e:
        return f"Failed to set frequency: {e}"
