"""CLI entrypoint for running agents.

Usage:
    python3 -m agents.runner daily-briefing     # run one agent
    python3 -m agents.runner --list             # list agents + schedules
    python3 -m agents.runner --install-cron     # write crontab entries
    python3 -m agents.runner --history <agent>  # show recent runs

Travel agent (on-demand, two modes):
    python3 -m agents travel-agent --destination "Barcelona" --origin "Dublin" --checkin 2026-07-01 --checkout 2026-07-07
    python3 -m agents travel-agent --mode plan --destination "Barcelona" --checkin 2026-07-01 --checkout 2026-07-07 \
        --flights "Ryanair FR1234 DUB->BCN 06:30, return FR1235" --hotel "H10 Marina, 7 nights"
"""

import argparse
import subprocess
import sys
from pathlib import Path

from .db import AgentDB, DEFAULT_DB_PATH

REPO_ROOT = Path(__file__).resolve().parent.parent

# Registry of all agents — import lazily to avoid circular imports
AGENT_REGISTRY = {
    "daily-briefing": "agents.daily_briefing:DailyBriefingAgent",
    "news-briefing": "agents.news_briefing:NewsBriefingAgent",
    "security-audit": "agents.security_audit:SecurityAuditAgent",
    "travel-agent": "agents.travel_agent:TravelAgent",
    "librarian": "agents.librarian:LibrarianAgent",
    "plant-agent": "agents.plant_agent:PlantAgent",
    "agent-health": "agents.agent_health:AgentHealthAgent",
}


def _load_agent(name: str):
    if name not in AGENT_REGISTRY:
        print(f"Unknown agent: {name}", file=sys.stderr)
        print(f"Available: {', '.join(AGENT_REGISTRY)}", file=sys.stderr)
        sys.exit(1)
    module_path, class_name = AGENT_REGISTRY[name].rsplit(":", 1)
    import importlib
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def _all_agents():
    """Load all agent classes."""
    agents = []
    for name in AGENT_REGISTRY:
        cls = _load_agent(name)
        agents.append(cls)
    return agents


def cmd_run(args):
    cls = _load_agent(args.agent)
    agent = cls()

    # Security audit --fix mode
    if getattr(args, "fix", False):
        if not hasattr(agent, "run_fix_mode"):
            print(f"Agent '{args.agent}' does not support --fix mode", file=sys.stderr)
            sys.exit(1)
        agent.run_fix_mode()
        return

    # Pass args to agents that support configure() (e.g. travel-agent)
    if hasattr(agent, "configure"):
        if args.agent == "travel-agent":
            if not args.destination or not args.checkin or not args.checkout:
                print("travel-agent requires --destination, --checkin, and --checkout", file=sys.stderr)
                sys.exit(1)
        agent.configure(args)

    try:
        output = agent.run()
        print(output)
    except Exception as e:
        print(f"Agent '{args.agent}' failed: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_list(args):
    agents = _all_agents()
    print(f"{'AGENT':<25} {'SCHEDULE':<20}")
    print(f"{'-----':<25} {'--------':<20}")
    for cls in agents:
        schedule = cls.schedule or "(on-demand)"
        print(f"{cls.name:<25} {schedule:<20}")


def cmd_history(args):
    db = AgentDB()
    runs = db.get_run_history(args.agent, limit=args.limit)
    if not runs:
        print(f"No runs found for '{args.agent}'")
        return
    print(f"{'ID':<6} {'STARTED':<22} {'STATUS':<10} {'SUMMARY'}")
    print(f"{'--':<6} {'-------':<22} {'------':<10} {'-------'}")
    for r in runs:
        summary = (r["output_summary"] or r["error"] or "")[:60]
        print(f"{r['id']:<6} {r['started_at']:<22} {r['status']:<10} {summary}")
    db.close()


def cmd_install_cron(args):
    agents = _all_agents()
    run_agent = REPO_ROOT / "run-agent.sh"
    log_file = REPO_ROOT / "output" / "cron.log"

    lines = []
    for cls in agents:
        if cls.schedule:
            lines.append(
                f"{cls.schedule} {run_agent} {cls.name} >> {log_file} 2>&1"
            )

    if not lines:
        print("No agents with schedules found.")
        return

    # Read existing crontab, replace agent entries
    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        existing = result.stdout if result.returncode == 0 else ""
    except FileNotFoundError:
        existing = ""

    # Remove old agent entries
    marker_start = "# --- ai-agents managed ---"
    marker_end = "# --- end ai-agents ---"
    filtered = []
    skipping = False
    for line in existing.splitlines():
        if line.strip() == marker_start:
            skipping = True
            continue
        if line.strip() == marker_end:
            skipping = False
            continue
        if not skipping:
            filtered.append(line)

    # Add new entries
    filtered.append(marker_start)
    for line in lines:
        filtered.append(line)
    filtered.append(marker_end)

    new_crontab = "\n".join(filtered) + "\n"
    proc = subprocess.run(["crontab", "-"], input=new_crontab, text=True)
    if proc.returncode == 0:
        print("Crontab updated:")
        for line in lines:
            print(f"  {line}")
    else:
        print("Failed to update crontab", file=sys.stderr)
        sys.exit(1)


def cmd_librarian_apply(args):
    from .librarian import apply_proposal
    sys.exit(apply_proposal(args.id))


def cmd_librarian_reject(args):
    from .librarian import reject_proposal
    sys.exit(reject_proposal(args.id))


def cmd_librarian_plan(args):
    from .librarian import create_plan_from_proposal
    sys.exit(create_plan_from_proposal(args.id))


def main():
    parser = argparse.ArgumentParser(description="Agent runner")
    subparsers = parser.add_subparsers(dest="command")

    # Default: run an agent by name (positional)
    run_parser = subparsers.add_parser("run", help="Run an agent")
    run_parser.add_argument("agent", help="Agent name")
    run_parser.add_argument("--fix", action="store_true", help="Interactive fix mode (security-audit)")
    run_parser.add_argument("--force", action="store_true", help="Clear dedup and force re-run (daily-briefing)")
    # Travel agent args (ignored by other agents)
    run_parser.add_argument(
        "--mode",
        choices=["search", "plan", "audit", "watch"],
        default="search",
        help="Mode: travel-agent uses search/plan; librarian uses audit/watch",
    )
    run_parser.add_argument("--destination", help="travel-agent: destination city/country")
    run_parser.add_argument("--origin", help="travel-agent (search mode): departure city")
    run_parser.add_argument("--checkin", help="travel-agent: check-in date (YYYY-MM-DD)")
    run_parser.add_argument("--checkout", help="travel-agent: check-out date (YYYY-MM-DD)")
    run_parser.add_argument("--flights", help="travel-agent (plan mode): existing flight details as free text")
    run_parser.add_argument("--hotel", help="travel-agent (plan mode): existing hotel booking as free text")

    # List agents
    subparsers.add_parser("list", help="List all agents and schedules")

    # History
    hist_parser = subparsers.add_parser("history", help="Show recent runs")
    hist_parser.add_argument("agent", help="Agent name")
    hist_parser.add_argument("--limit", type=int, default=10)

    # Install cron
    subparsers.add_parser("install-cron", help="Write crontab entries for all agents")

    # Librarian proposal actions (replace the removed MCP bridge approve/reject links)
    for action, helptext in (
        ("librarian-apply", "Apply a pending librarian prompt_edit proposal by id"),
        ("librarian-reject", "Reject a librarian proposal by id"),
        ("librarian-plan", "Materialise a librarian architecture_plan proposal by id"),
    ):
        p = subparsers.add_parser(action, help=helptext)
        p.add_argument("id", help="Proposal id")

    _KNOWN = ("run", "list", "history", "install-cron",
              "librarian-apply", "librarian-reject", "librarian-plan", "-h", "--help")
    # Allow `python -m agents daily-briefing` as shorthand for `run`
    if len(sys.argv) > 1 and sys.argv[1] not in _KNOWN:
        sys.argv.insert(1, "run")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    cmds = {
        "run": cmd_run,
        "list": cmd_list,
        "history": cmd_history,
        "install-cron": cmd_install_cron,
        "librarian-apply": cmd_librarian_apply,
        "librarian-reject": cmd_librarian_reject,
        "librarian-plan": cmd_librarian_plan,
    }
    cmds[args.command](args)


if __name__ == "__main__":
    main()
