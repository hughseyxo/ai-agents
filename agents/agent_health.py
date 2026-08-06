"""Agent Health monitor — deterministic staleness detection.

Watches every scheduled agent and pushes a Telegram alert when one stops
running (e.g. its cron entry was dropped, or it errors repeatedly). An agent
is "stale" when its last healthy run is older than 2x its expected interval,
derived from its cron schedule. Alerts are deduplicated via agent state so a
stale agent is reported once, with a recovery message when it runs again.

Fully deterministic — no LLM. Runs hourly so a 2h-stale hourly agent surfaces
quickly.
"""

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .base import BaseAgent
from .telegram_client import send_telegram

STALE_FACTOR = 2
# Statuses that mean the agent actually executed to completion.
HEALTHY_STATUSES = ("success", "partial_failure")
TEXTFILE_COLLECTOR_DIR = Path("/var/lib/node_exporter/textfile_collector")


def cron_interval_seconds(schedule: str) -> int:
    """Estimate the interval between runs of a 5-field cron schedule, in seconds.

    Handles the common cases used by this project's agents. Specificity is
    checked coarsest-first: a specific day-of-week => weekly, etc.
    """
    parts = schedule.split()
    if len(parts) != 5:
        raise ValueError(f"Unsupported cron schedule: {schedule!r}")
    minute, hour, dom, month, dow = parts

    if dow != "*":
        return 7 * 86400
    if dom != "*":
        return 30 * 86400
    if hour.startswith("*/"):
        return int(hour[2:]) * 3600
    if hour != "*":
        return 86400  # fires at a specific hour each day
    # hour is wildcard
    if minute.startswith("*/"):
        return int(minute[2:]) * 60
    if minute == "*":
        return 60
    return 3600  # specific minute every hour


def evaluate_staleness(
    agents_state: dict, now: datetime, factor: int = STALE_FACTOR
) -> list:
    """Return sorted names of stale agents.

    agents_state maps agent name -> (cron_schedule, last_healthy_run_dt | None).
    An agent is stale if it has never had a healthy run, or its last healthy
    run is older than `factor` x its cron interval.
    """
    stale = []
    for name, (schedule, last) in agents_state.items():
        if last is None:
            stale.append(name)
            continue
        threshold = cron_interval_seconds(schedule) * factor
        if (now - last).total_seconds() > threshold:
            stale.append(name)
    return sorted(stale)


def diff_alerts(stale: list, previously_alerted: list):
    """Split current stale set against the previously-alerted set.

    Returns (new_alerts, recovered) — both sorted.
    """
    stale_set = set(stale)
    prev_set = set(previously_alerted)
    new_alerts = sorted(stale_set - prev_set)
    recovered = sorted(prev_set - stale_set)
    return new_alerts, recovered


def _humanize(delta_seconds: float) -> str:
    if delta_seconds < 3600:
        return f"{int(delta_seconds // 60)}m"
    if delta_seconds < 86400:
        return f"{delta_seconds / 3600:.1f}h"
    return f"{delta_seconds / 86400:.1f}d"


class AgentHealthAgent(BaseAgent):
    name = "agent-health"
    schedule = "0 * * * *"  # hourly — deterministic, no LLM

    def plan(self):
        return {"today": datetime.now(timezone.utc).strftime("%Y-%m-%d")}

    def steps(self):
        return [{"name": "check", "fn": self._check, "side_effects": True}]

    def report(self) -> str:
        c = self.context.get("check", {})
        return (
            f"agent-health: {c.get('checked', 0)} checked, "
            f"{len(c.get('stale', []))} stale, "
            f"{c.get('alerts_sent', 0)} alert(s) sent"
        )

    # --- core ---

    def _monitored(self) -> dict:
        """Build {name: (schedule, last_healthy_run_dt|None)} for all scheduled
        agents except self."""
        from .runner import AGENT_REGISTRY, _load_agent

        out = {}
        for name in AGENT_REGISTRY:
            cls = _load_agent(name)
            if not cls.schedule or cls.name == self.name:
                continue
            out[cls.name] = (cls.schedule, self._last_healthy_run(cls.name))
        return out

    def _last_healthy_run(self, agent_name: str):
        for run in self.db.get_run_history(agent_name, limit=20):
            if run["status"] in HEALTHY_STATUSES:
                ts = _parse_ts(run["started_at"])
                if ts:
                    return ts
        return None

    def _check(self):
        now = datetime.now(timezone.utc)
        monitored = self._monitored()
        stale = evaluate_staleness(monitored, now)

        previously = self.get_state("alerted") or []
        new_alerts, recovered = diff_alerts(stale, previously)

        sent_names = []
        for name in new_alerts:
            schedule, last = monitored[name]
            if last is None:
                detail = "no healthy run on record"
            else:
                detail = f"last healthy run {_humanize((now - last).total_seconds())} ago"
            msg = (
                f"⚠️ Agent health: `{name}` is stale — {detail} "
                f"(schedule `{schedule}`). Check its cron entry / logs."
            )
            if self._send_telegram(msg):
                sent_names.append(name)

        for name in recovered:
            self._send_telegram(f"✅ Agent `{name}` is healthy again.")

        # Only record what we actually notified: still-stale agents already alerted,
        # plus newly-alerted agents whose Telegram send succeeded. A failed new alert
        # is left unrecorded so the next run retries it instead of going silent.
        still_alerted = [n for n in stale if n in previously] + sent_names
        self.set_state("alerted", still_alerted)
        write_health_metric(TEXTFILE_COLLECTOR_DIR, now.timestamp())
        return {"checked": len(monitored), "stale": stale, "alerts_sent": len(sent_names)}

    # --- telegram ---

    def _send_telegram(self, text: str) -> bool:
        return send_telegram(text)


def write_health_metric(directory: Path, timestamp: float) -> None:
    """Write agent-health's own last-success timestamp for node-exporter's
    textfile collector to scrape. Atomic (tempfile + os.replace) — safe here
    because the target is a whole-directory hostPath mount, not the
    single-file `type: File` mount that caused Phase 4's EBUSY bug."""
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "agent_health.prom"
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".agent_health-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(f"agent_health_last_success_timestamp {int(timestamp)}\n")
        os.replace(tmp_path, target)
    except BaseException:
        os.unlink(tmp_path)
        raise


def _parse_ts(s: str):
    """Parse a runs.started_at timestamp ('YYYY-MM-DD HH:MM:SS') as UTC."""
    if not s:
        return None
    s = s.strip().replace("T", " ")[:19]
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
