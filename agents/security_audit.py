"""Security Audit Agent.

Weekly read-only agent that scans the server for vulnerabilities and produces
a prioritized markdown report. All checks are deterministic system command
parsing — no Claude CLI or MCP tools.
"""

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .base import BaseAgent, REPO_ROOT

SEVERITY_ORDER = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}

SERVER_CONTEXT = {
    "seedbox_compose": Path.home() / "git/yopflix/seedbox",
    "traefik_config": Path.home() / "git/yopflix/seedbox/traefik",
    "expected_public_ports": {"80", "443"},
    "tailscale_interface": "tailscale0",
    "ssh_access": "tailscale_only",
    "services_with_own_auth": {"jellyfin", "jellyseerr", "mealie", "homeassistant"},
    "expected_services": {
        "traefik", "deluge", "nzbget", "gluetun", "sonarr", "ymir",
        "radarr", "bazarr", "prowlarr", "jellyfin", "jellyseerr",
        "mealie", "byparr", "flaresolverr", "watchtower", "maintainerr",
        "homeassistant", "eufy-security-ws",
    },
}


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a read-only system command, returning result even on failure."""
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30, **kwargs)


class SecurityAuditAgent(BaseAgent):
    name = "security-audit"
    schedule = "0 6 * * 0"  # Sunday 06:00 UTC (08:00 CEST)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.findings: list[dict] = []
        self.passed: list[str] = []

    def steps(self):
        return [
            {"name": "ssh_hardening", "fn": self._check_ssh},
            {"name": "open_ports", "fn": self._check_ports},
            {"name": "firewall", "fn": self._check_firewall},
            {"name": "file_permissions", "fn": self._check_file_permissions},
            {"name": "unattended_upgrades", "fn": self._check_unattended_upgrades},
            {"name": "user_accounts", "fn": self._check_users},
            {"name": "systemd_services", "fn": self._check_systemd},
            {"name": "secrets_hygiene", "fn": self._check_secrets},
            {"name": "ssl_tls", "fn": self._check_ssl},
            {"name": "docker", "fn": self._check_docker},
            {"name": "fail2ban", "fn": self._check_fail2ban},
            {"name": "cron_jobs", "fn": self._check_cron},
            {"name": "traefik_security", "fn": self._check_traefik},
            {"name": "tailscale_network", "fn": self._check_tailscale_network},
            {"name": "public_route_auth", "fn": self._check_public_route_auth},
            {"name": "git_secret_scan", "fn": self._check_git_secrets},
            {"name": "cloudflare_ips", "fn": self._check_cloudflare_ips},
            {"name": "shodan_exposure", "fn": self._check_shodan_exposure},
        ]

    def report(self) -> str:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.findings.sort(key=lambda f: SEVERITY_ORDER.get(f["severity"], 99))

        lines = [
            f"# Security Audit Report — {today}",
            "",
            f"**Generated:** {datetime.now(timezone.utc).isoformat()}",
            f"**Findings:** {len(self.findings)} | **Passed:** {len(self.passed)}",
            "",
        ]

        # Summary counts
        for sev in ("Critical", "High", "Medium", "Low"):
            count = sum(1 for f in self.findings if f["severity"] == sev)
            if count:
                lines.append(f"- **{sev}:** {count}")
        lines.append("")

        # Findings
        if self.findings:
            lines.append("## Findings")
            lines.append("")
            for i, f in enumerate(self.findings, 1):
                lines.append(f"### {i}. [{f['severity']}] {f['check']}")
                lines.append("")
                lines.append(f"**Detail:** {f['detail']}")
                lines.append("")
                lines.append(f"**Why it matters:** {f['context']}")
                lines.append("")
                lines.append(f"**Risk:** {f['risk']}")
                lines.append("")
                if f.get("fix_commands"):
                    lines.append("**Fix:**")
                    lines.append("```bash")
                    for cmd in f["fix_commands"]:
                        lines.append(cmd)
                    lines.append("```")
                else:
                    lines.append("**Fix:** [Manual action required]")
                lines.append("")
                lines.append(f"**Impact:** {f['impact']}")
                lines.append("")

        # Passed checks
        if self.passed:
            lines.append("## Passed")
            lines.append("")
            for p in self.passed:
                lines.append(f"- {p}")
            lines.append("")

        report_text = "\n".join(lines)
        output_path = REPO_ROOT / "output" / f"security-audit-{today}.md"
        output_path.write_text(report_text)
        return report_text

    def _finding(self, severity: str, check: str, detail: str, context: str,
                 risk: str, impact: str, fix_commands: list[str] | None = None):
        self.findings.append({
            "severity": severity,
            "check": check,
            "detail": detail,
            "context": context,
            "risk": risk,
            "impact": impact,
            "fix_commands": fix_commands,
        })

    def _pass(self, check: str):
        self.passed.append(check)

    # --- Check 1: SSH hardening ---

    def _check_ssh(self):
        sshd_config = Path("/etc/ssh/sshd_config")
        if not sshd_config.exists():
            self._pass("SSH: sshd not installed")
            return

        config = sshd_config.read_text()
        # Also read config fragments
        conf_d = Path("/etc/ssh/sshd_config.d")
        if conf_d.is_dir():
            for f in sorted(conf_d.glob("*.conf")):
                config += "\n" + f.read_text()

        issues = []

        # Check PermitRootLogin
        match = re.search(r"^\s*PermitRootLogin\s+(\S+)", config, re.MULTILINE)
        if not match or match.group(1).lower() not in ("no", "prohibit-password"):
            issues.append("Root login is not disabled")

        # Check PasswordAuthentication
        match = re.search(r"^\s*PasswordAuthentication\s+(\S+)", config, re.MULTILINE)
        if not match or match.group(1).lower() != "no":
            issues.append("Password authentication is enabled")

        # Check PubkeyAuthentication
        match = re.search(r"^\s*PubkeyAuthentication\s+(\S+)", config, re.MULTILINE)
        if match and match.group(1).lower() == "no":
            issues.append("Public key authentication is disabled")

        if issues:
            self._finding(
                severity="High",
                check="SSH hardening",
                detail="; ".join(issues),
                context="SSH is the primary remote access method — weak config exposes the server to brute-force or credential attacks",
                risk="Unauthorized access via password guessing or compromised root credentials",
                impact="Disabling password auth and root login forces key-based auth only; existing key-based sessions are unaffected",
                fix_commands=[
                    "sudo sed -i 's/^#\\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config",
                    "sudo sed -i 's/^#\\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config",
                    "sudo systemctl reload sshd",
                ],
            )
        else:
            self._pass("SSH hardening: key-only auth, root login disabled")

        # Check UFW SSH rule — SSH accessed via Tailscale, broad allow is a hardening opportunity
        ufw = _run(["sudo", "ufw", "status"])
        if ufw.returncode == 0 and "22" in ufw.stdout:
            # Check if SSH is allowed from anywhere vs restricted to tailscale
            if "22/tcp" in ufw.stdout and "Anywhere" in ufw.stdout:
                # Check it's not already restricted to tailscale interface
                lines = ufw.stdout.splitlines()
                broad_ssh = any("22" in l and "Anywhere" in l and "tailscale0" not in l
                                for l in lines)
                if broad_ssh:
                    self._finding(
                        severity="Medium",
                        check="SSH UFW hardening",
                        detail="UFW allows SSH (port 22) from anywhere; SSH is accessed exclusively via Tailscale",
                        context="Since SSH access is via Tailscale only, restricting UFW to the tailscale0 interface reduces attack surface",
                        risk="If Tailscale were bypassed, SSH would still be reachable from the public internet",
                        impact="Restricting to tailscale0 means SSH is only reachable via Tailscale; if Tailscale goes down, SSH becomes inaccessible",
                        fix_commands=[
                            "sudo ufw delete allow 22/tcp",
                            "sudo ufw allow in on tailscale0 to any port 22 proto tcp",
                        ],
                    )

    # --- Check 2: Open ports & services ---

    def _check_ports(self):
        result = _run(["ss", "-tlnp"])
        if result.returncode != 0:
            self._pass("Open ports: ss command not available")
            return

        # Get UFW rules to determine which ports are actually publicly reachable
        ufw = _run(["sudo", "ufw", "status"])
        ufw_output = ufw.stdout if ufw.returncode == 0 else ""

        unexpected = []
        tailscale_only = []
        for line in result.stdout.splitlines()[1:]:  # skip header
            parts = line.split()
            if len(parts) < 4:
                continue
            local_addr = parts[3]
            # Flag anything listening on 0.0.0.0 or :: that isn't SSH (22), HTTP(S) (80/443)
            if "*:" in local_addr or "0.0.0.0:" in local_addr or ":::" in local_addr:
                port = local_addr.rsplit(":", 1)[-1]
                if port not in ("22", "80", "443"):
                    process = parts[-1] if len(parts) > 5 else "unknown"
                    # Check if this port is blocked by UFW (making it Tailscale-only)
                    port_in_ufw = port in ufw_output
                    if port_in_ufw:
                        unexpected.append(f"port {port} ({process})")
                    else:
                        tailscale_only.append(f"port {port} ({process})")

        if unexpected:
            self._finding(
                severity="High",
                check="Open ports & services",
                detail=f"Services on all interfaces with UFW allow rules: {', '.join(unexpected)}",
                context="These ports are publicly reachable through UFW — only intentionally public services should be allowed",
                risk="Unintended exposure of internal services to the internet",
                impact="Removing UFW allow rules restricts access to Tailscale-only",
                fix_commands=None,
            )

        if tailscale_only:
            self._finding(
                severity="Low",
                check="Open ports (Tailscale-only)",
                detail=f"Services on all interfaces but UFW-blocked (effectively Tailscale-only): {', '.join(tailscale_only)}",
                context="These ports are bound to 0.0.0.0 but UFW blocks public access — they're reachable only via Tailscale. Security depends on UFW staying active",
                risk="If UFW is disabled, these services become publicly reachable",
                impact="Informational — no action needed while UFW is active",
            )

        if not unexpected and not tailscale_only:
            self._pass("Open ports: only expected services (22, 80, 443) on public interfaces")

    # --- Check 3: Firewall ---

    def _check_firewall(self):
        # Try ufw first
        ufw = _run(["sudo", "ufw", "status", "verbose"])
        if ufw.returncode == 0:
            if "inactive" in ufw.stdout.lower():
                self._finding(
                    severity="High",
                    check="Firewall (ufw)",
                    detail="ufw is installed but inactive",
                    context="A firewall is the first line of defense — without it, all ports are exposed to the network",
                    risk="Any service bound to a public interface is directly reachable",
                    impact="Enabling ufw with default-deny will block all inbound traffic except explicitly allowed ports",
                    fix_commands=[
                        "sudo ufw default deny incoming",
                        "sudo ufw default allow outgoing",
                        "sudo ufw allow ssh",
                        "sudo ufw --force enable",
                    ],
                )
                return

            # UFW is active — verify Cloudflare-only rules for 80/443
            ufw_text = ufw.stdout
            if "80" in ufw_text or "443" in ufw_text:
                # Check if 80/443 are restricted to Cloudflare IPs or open to Anywhere
                lines = ufw_text.splitlines()
                broad_http = any(
                    ("80" in l or "443" in l) and "Anywhere" in l and "cloudflare" not in l.lower()
                    for l in lines
                    if not l.strip().startswith("#")
                )
                if broad_http:
                    self._finding(
                        severity="Medium",
                        check="Firewall: Cloudflare restriction",
                        detail="Ports 80/443 are allowed from Anywhere — should be restricted to Cloudflare IP ranges only",
                        context="Traffic should flow Cloudflare → UFW → Traefik; allowing all IPs lets attackers bypass Cloudflare protections",
                        risk="Direct access bypasses Cloudflare DDoS protection, WAF rules, and rate limiting",
                        impact="Restricting to Cloudflare IPs means direct IP access stops working (which is the goal)",
                    )

            self._pass("Firewall: ufw active")
            return

        # Try iptables
        ipt = _run(["sudo", "iptables", "-L", "-n"])
        if ipt.returncode == 0 and "ACCEPT" in ipt.stdout:
            self._pass("Firewall: iptables rules present")
            return

        self._finding(
            severity="High",
            check="Firewall",
            detail="No active firewall detected (ufw/iptables)",
            context="Without a firewall, every listening service is exposed to the network",
            risk="Attackers can probe and connect to any open port",
            impact="Installing and enabling ufw will restrict inbound traffic to allowed ports only",
            fix_commands=[
                "sudo apt install -y ufw",
                "sudo ufw default deny incoming",
                "sudo ufw default allow outgoing",
                "sudo ufw allow ssh",
                "sudo ufw --force enable",
            ],
        )

    # --- Check 4: File permissions ---

    def _check_file_permissions(self):
        issues = []

        # Check sensitive files
        sensitive_files = [
            (REPO_ROOT / ".env", "0600"),
            (REPO_ROOT / "credentials.json", "0600"),
            (Path.home() / ".ssh" / "id_rsa", "0600"),
            (Path.home() / ".ssh" / "id_ed25519", "0600"),
        ]
        for path, expected_mode in sensitive_files:
            if path.exists():
                mode = oct(path.stat().st_mode)[-4:]
                if mode != expected_mode and int(mode, 8) > int(expected_mode, 8):
                    issues.append(f"{path} has mode {mode} (should be {expected_mode})")

        # World-writable files in project
        result = _run(["find", str(REPO_ROOT), "-maxdepth", "3", "-perm", "-o+w",
                        "-not", "-path", "*/.git/*", "-not", "-path", "*/node_modules/*",
                        "-not", "-type", "l"])
        if result.returncode == 0 and result.stdout.strip():
            ww_files = [f for f in result.stdout.strip().splitlines() if f]
            if ww_files:
                issues.append(f"World-writable files: {', '.join(ww_files[:5])}")

        # SUID binaries outside expected set
        result = _run(["find", "/usr/bin", "/usr/sbin", "-perm", "-4000", "-type", "f"])
        if result.returncode == 0:
            expected_suid = {"sudo", "su", "passwd", "chsh", "chfn", "newgrp", "gpasswd",
                             "mount", "umount", "pkexec", "fusermount", "fusermount3"}
            for line in result.stdout.strip().splitlines():
                binary = Path(line.strip()).name
                if binary and binary not in expected_suid:
                    issues.append(f"Unexpected SUID binary: {line.strip()}")

        if issues:
            self._finding(
                severity="Critical" if any(".env" in i or "credentials" in i or "id_rsa" in i for i in issues) else "Medium",
                check="File permissions",
                detail="; ".join(issues),
                context="Overly permissive files can leak secrets to other users or processes on the system",
                risk="Credential theft, privilege escalation via SUID binaries, or data exposure",
                impact="Tightening permissions may break scripts that rely on group/other read access",
                fix_commands=[f"chmod 600 {path}" for path, _ in sensitive_files if path.exists()],
            )
        else:
            self._pass("File permissions: sensitive files properly restricted, no unexpected SUID binaries")

    # --- Check 5: Unattended upgrades ---

    def _check_unattended_upgrades(self):
        # Check if installed
        dpkg = _run(["dpkg", "-l", "unattended-upgrades"])
        if dpkg.returncode != 0 or "ii" not in dpkg.stdout:
            self._finding(
                severity="High",
                check="Unattended upgrades",
                detail="unattended-upgrades package is not installed",
                context="Security patches are released frequently — manual updates are easily missed",
                risk="Known vulnerabilities remain unpatched, making the server an easy target",
                impact="Automatic updates may occasionally require a reboot; rare risk of a breaking update",
                fix_commands=[
                    "sudo apt install -y unattended-upgrades",
                    "sudo dpkg-reconfigure -plow unattended-upgrades",
                ],
            )
            return

        # Check if enabled
        config = Path("/etc/apt/apt.conf.d/20auto-upgrades")
        if config.exists():
            content = config.read_text()
            if '"1"' in content:
                # Check for pending security updates
                result = _run(["apt", "list", "--upgradable"])
                pending = [l for l in result.stdout.splitlines() if "security" in l.lower()] if result.returncode == 0 else []
                if pending:
                    self._finding(
                        severity="Medium",
                        check="Unattended upgrades",
                        detail=f"{len(pending)} pending security update(s)",
                        context="These patches are queued but not yet applied",
                        risk="Delayed patching leaves a window of vulnerability",
                        impact="Applying updates now may require a service restart",
                        fix_commands=["sudo apt upgrade -y"],
                    )
                else:
                    self._pass("Unattended upgrades: installed, enabled, no pending security patches")
                return

        self._finding(
            severity="High",
            check="Unattended upgrades",
            detail="unattended-upgrades is installed but not enabled",
            context="The package exists but automatic updates are not configured",
            risk="Same as having no automatic updates — patches are missed",
            impact="Enabling auto-updates adds a small risk of breaking changes from updates",
            fix_commands=["sudo dpkg-reconfigure -plow unattended-upgrades"],
        )

    # --- Check 6: User accounts ---

    def _check_users(self):
        issues = []

        # UID 0 users besides root
        with open("/etc/passwd") as f:
            for line in f:
                parts = line.strip().split(":")
                if len(parts) >= 4 and parts[2] == "0" and parts[0] != "root":
                    issues.append(f"Non-root user with UID 0: {parts[0]}")

        # Users with login shells that aren't expected
        expected_shell_users = {"root", os.getenv("USER", ""), "ubuntu", "cian"}
        login_shells = {"/bin/bash", "/bin/zsh", "/bin/sh", "/usr/bin/zsh", "/usr/bin/bash"}
        with open("/etc/passwd") as f:
            for line in f:
                parts = line.strip().split(":")
                if len(parts) >= 7:
                    user, shell = parts[0], parts[6]
                    if shell in login_shells and user not in expected_shell_users:
                        issues.append(f"Unexpected user with login shell: {user} ({shell})")

        # Empty passwords
        try:
            with open("/etc/shadow") as f:
                for line in f:
                    parts = line.strip().split(":")
                    if len(parts) >= 2 and parts[1] == "":
                        issues.append(f"User with empty password: {parts[0]}")
        except PermissionError:
            pass  # can't read shadow without root

        if issues:
            self._finding(
                severity="Critical" if any("UID 0" in i or "empty password" in i for i in issues) else "Medium",
                check="User accounts",
                detail="; ".join(issues),
                context="Extra privileged or shell-enabled accounts expand the attack surface",
                risk="Compromised or forgotten accounts provide unauthorized access paths",
                impact="Locking or removing accounts may break services that run under them — verify first",
            )
        else:
            self._pass("User accounts: no unexpected UID 0 users, login shells, or empty passwords")

    # --- Check 7: Systemd services ---

    def _check_systemd(self):
        result = _run(["systemctl", "list-units", "--type=service", "--state=running", "--no-pager", "--no-legend"])
        if result.returncode != 0:
            self._pass("Systemd services: could not enumerate")
            return

        # Services that commonly shouldn't run as root
        should_not_be_root = {"nginx", "apache2", "postgresql", "mysql", "redis", "node", "python"}
        root_services = []

        for line in result.stdout.strip().splitlines():
            unit = line.split()[0] if line.split() else ""
            if not unit.endswith(".service"):
                continue
            # Check if service runs as root by inspecting the unit
            show = _run(["systemctl", "show", unit, "--property=User"])
            user = ""
            if show.returncode == 0:
                for prop in show.stdout.strip().splitlines():
                    if prop.startswith("User="):
                        user = prop.split("=", 1)[1]
            service_base = unit.replace(".service", "")
            if not user and any(svc in service_base for svc in should_not_be_root):
                root_services.append(service_base)

        if root_services:
            self._finding(
                severity="Medium",
                check="Systemd services",
                detail=f"Services running as root without User= directive: {', '.join(root_services)}",
                context="Running services as root gives them full system access — a compromised service becomes a full compromise",
                risk="Privilege escalation from service vulnerability to root access",
                impact="Adding User= to unit files requires the target user to exist and have file access",
            )
        else:
            self._pass("Systemd services: no unnecessary root-running services detected")

    # --- Check 8: Secrets hygiene ---

    def _check_secrets(self):
        issues = []

        # .env file permissions (already partially covered in check 4, but check existence here)
        env_file = REPO_ROOT / ".env"
        if env_file.exists():
            mode = oct(env_file.stat().st_mode)[-4:]
            if int(mode, 8) > 0o600:
                issues.append(f".env file too permissive: {mode}")

        # Tokens in git history (check last 50 commits for common patterns)
        result = _run(["git", "-C", str(REPO_ROOT), "log", "--all", "-50", "--diff-filter=A",
                        "-p", "--", "*.env", "*.key", "*.pem"])
        if result.returncode == 0 and result.stdout.strip():
            # Look for actual secret patterns
            secret_patterns = [
                r"(?:API_KEY|SECRET|TOKEN|PASSWORD|PRIVATE_KEY)\s*=\s*\S+",
                r"sk-[a-zA-Z0-9]{20,}",
                r"ghp_[a-zA-Z0-9]{36}",
            ]
            for pattern in secret_patterns:
                if re.search(pattern, result.stdout):
                    issues.append("Potential secrets found in git history (*.env, *.key, *.pem files)")
                    break

        # Tokens in bash history
        history_file = Path.home() / ".bash_history"
        if history_file.exists():
            try:
                history = history_file.read_text(errors="replace")
                for pattern in [r"export\s+\w*(TOKEN|SECRET|KEY|PASSWORD)\w*=\S+",
                                r"curl.*-H.*[Aa]uthorization.*Bearer\s+\S+"]:
                    if re.search(pattern, history):
                        issues.append("Secrets found in bash history")
                        break
            except PermissionError:
                pass

        if issues:
            self._finding(
                severity="Critical" if "git history" in str(issues) else "High",
                check="Secrets hygiene",
                detail="; ".join(issues),
                context="Leaked secrets in version control or shell history persist even after rotation",
                risk="Credential theft from git history or history file; secrets may already be compromised",
                impact="Rotating secrets requires updating all services that use them",
                fix_commands=["chmod 600 .env"] if env_file.exists() else None,
            )
        else:
            self._pass("Secrets hygiene: no exposed tokens in git history or shell history")

    # --- Check 9: SSL/TLS ---

    def _check_ssl(self):
        # Check Let's Encrypt certs
        le_path = Path("/etc/letsencrypt/live")
        if le_path.is_dir():
            try:
                for domain_dir in le_path.iterdir():
                    cert = domain_dir / "fullchain.pem"
                    if cert.exists():
                        result = _run(["openssl", "x509", "-in", str(cert), "-noout", "-enddate"])
                        if result.returncode == 0:
                            date_str = result.stdout.strip().split("=", 1)[-1]
                            try:
                                expiry = datetime.strptime(date_str, "%b %d %H:%M:%S %Y %Z")
                                days_left = (expiry - datetime.utcnow()).days
                                if days_left < 14:
                                    self._finding(
                                        severity="Critical" if days_left < 0 else "High",
                                        check="SSL/TLS certificate expiry",
                                        detail=f"{domain_dir.name}: {'EXPIRED' if days_left < 0 else f'expires in {days_left} days'}",
                                        context="Expired certificates cause browser warnings and break HTTPS entirely",
                                        risk="Users see security warnings; HSTS-enabled sites become completely inaccessible",
                                        impact="Renewal requires port 80/443 briefly; no downtime for most setups",
                                        fix_commands=["sudo certbot renew --force-renewal"],
                                    )
                                    return
                            except ValueError:
                                pass
                self._pass("SSL/TLS: certificates valid with >14 days remaining")
                return
            except PermissionError:
                pass

        # Check Traefik acme.json for Let's Encrypt certs
        acme_json = SERVER_CONTEXT["traefik_config"] / "acme.json"
        if acme_json.exists():
            try:
                acme_data = json.loads(acme_json.read_text())
                # Traefik stores certs in acme.json — just verify file exists and has content
                if acme_data:
                    self._pass("SSL/TLS: Traefik acme.json present with certificate data")
                    return
            except (json.JSONDecodeError, PermissionError):
                pass

        # Check if any HTTPS service is running
        result = _run(["ss", "-tlnp"])
        if result.returncode == 0 and ":443" in result.stdout:
            self._pass("SSL/TLS: port 443 active (cert check requires root for Let's Encrypt)")
        else:
            self._pass("SSL/TLS: no HTTPS services detected (check not applicable)")

    # --- Check 10: Docker ---

    def _check_docker(self):
        result = _run(["docker", "ps", "--format", "{{.ID}}\t{{.Image}}\t{{.Names}}\t{{.Ports}}"])
        if result.returncode != 0:
            self._pass("Docker: not installed or not running")
            return

        if not result.stdout.strip():
            self._pass("Docker: no running containers")
            return

        issues = []
        for line in result.stdout.strip().splitlines():
            parts = line.split("\t")
            if len(parts) < 4:
                continue
            cid, image, name, ports = parts[0], parts[1], parts[2], parts[3]

            # Check if running as root — linuxserver.io images use PUID/PGID internally
            inspect = _run(["docker", "inspect", "--format", "{{.Config.User}}", cid])
            if inspect.returncode == 0 and not inspect.stdout.strip():
                if "linuxserver" in image.lower() or "lscr.io" in image.lower():
                    # Low severity — PUID/PGID mitigates
                    pass  # Don't flag linuxserver images individually
                elif name not in SERVER_CONTEXT["expected_services"]:
                    issues.append(f"Container '{name}' ({image}) running as root")

            # Check for exposed ports on 0.0.0.0
            if "0.0.0.0:" in ports:
                issues.append(f"Container '{name}' exposes ports on all interfaces: {ports}")

        # Check Docker socket mount
        inspect_all = _run(["docker", "ps", "--format", "{{.Names}}"])
        if inspect_all.returncode == 0:
            for container_name in inspect_all.stdout.strip().splitlines():
                mounts = _run(["docker", "inspect", "--format", "{{range .Mounts}}{{.Source}}:{{.Destination}}:{{.Mode}} {{end}}", container_name.strip()])
                if mounts.returncode == 0 and "/var/run/docker.sock" in mounts.stdout:
                    mode = "rw" if ":rw" in mounts.stdout.split("docker.sock")[0].rsplit(":", 1)[-1] + mounts.stdout.split("docker.sock")[1].split(" ")[0] else "ro"
                    # Check full mount string for rw
                    sock_section = [m for m in mounts.stdout.split() if "docker.sock" in m]
                    if sock_section:
                        is_rw = ":rw" in sock_section[0] or sock_section[0].count(":") < 2
                        if is_rw:
                            issues.append(f"Container '{container_name.strip()}' has Docker socket mounted read-write — full host control")

        # Check for host network mode
        for container_name in (inspect_all.stdout.strip().splitlines() if inspect_all.returncode == 0 else []):
            net = _run(["docker", "inspect", "--format", "{{.HostConfig.NetworkMode}}", container_name.strip()])
            if net.returncode == 0 and net.stdout.strip() == "host":
                issues.append(f"Container '{container_name.strip()}' uses network_mode: host")

        if issues:
            severity = "High" if any("docker.sock" in i.lower() for i in issues) else "Medium"
            self._finding(
                severity=severity,
                check="Docker containers",
                detail="; ".join(issues),
                context="Containers running as root or with Docker socket access can compromise the host; host network mode bypasses container network isolation",
                risk="Container escape with root privileges gives full host access; Docker socket RW = full host control; host network exposes all host ports",
                impact="Adding USER directive or port binding changes may require container rebuild",
            )
        else:
            self._pass("Docker: all containers use non-root users and restricted port bindings")

    # --- Check 11: Fail2ban ---

    def _check_fail2ban(self):
        result = _run(["systemctl", "is-active", "fail2ban"])
        if result.returncode != 0 or "active" not in result.stdout:
            dpkg = _run(["dpkg", "-l", "fail2ban"])
            installed = dpkg.returncode == 0 and "ii" in dpkg.stdout
            self._finding(
                severity="High",
                check="Fail2ban",
                detail="fail2ban is not active" + (" (but installed)" if installed else " (not installed)"),
                context="Fail2ban blocks IPs after repeated failed login attempts — essential for SSH protection",
                risk="Brute-force attacks can run unchecked against SSH and other services",
                impact="Fail2ban may temporarily lock out legitimate users who mistype passwords",
                fix_commands=[
                    "sudo apt install -y fail2ban" if not installed else None,
                    "sudo systemctl enable --now fail2ban",
                ],
            )
            self.findings[-1]["fix_commands"] = [c for c in self.findings[-1]["fix_commands"] if c]
            return

        # Check SSH jail
        result = _run(["sudo", "fail2ban-client", "status", "sshd"])
        if result.returncode != 0:
            self._finding(
                severity="Medium",
                check="Fail2ban SSH jail",
                detail="fail2ban is running but sshd jail is not active",
                context="The SSH jail is the most important fail2ban jail for server protection",
                risk="SSH brute-force attempts are not being blocked",
                impact="Enabling the sshd jail adds automated IP banning for failed SSH logins",
                fix_commands=[
                    "echo -e '[sshd]\\nenabled = true' | sudo tee /etc/fail2ban/jail.local",
                    "sudo systemctl restart fail2ban",
                ],
            )
        else:
            self._pass("Fail2ban: active with SSH jail enabled")

    # --- Check 12: Cron jobs ---

    def _check_cron(self):
        issues = []

        # System cron directories
        cron_dirs = ["/etc/cron.d", "/etc/cron.daily", "/etc/cron.hourly",
                     "/etc/cron.weekly", "/etc/cron.monthly"]
        for d in cron_dirs:
            p = Path(d)
            if not p.is_dir():
                continue
            for f in p.iterdir():
                if f.is_file():
                    mode = oct(f.stat().st_mode)[-4:]
                    if int(mode, 8) & 0o002:
                        issues.append(f"World-writable cron script: {f}")

        # User crontab — check for suspicious entries
        result = _run(["crontab", "-l"])
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                line = line.strip()
                if line.startswith("#") or not line:
                    continue
                # Flag curl/wget piped to shell
                if re.search(r"(curl|wget)\s.*\|\s*(bash|sh|zsh)", line):
                    issues.append(f"Cron entry pipes remote content to shell: {line[:80]}")

        if issues:
            self._finding(
                severity="Critical" if "World-writable" in str(issues) else "High",
                check="Cron jobs",
                detail="; ".join(issues),
                context="Cron runs with the owner's privileges — tampered scripts execute silently on schedule",
                risk="Privilege escalation or persistent backdoor via modified cron scripts",
                impact="Fixing permissions may require checking that cron service can still read the files",
            )
        else:
            self._pass("Cron jobs: no world-writable scripts or suspicious entries")

    # --- Check 13: Traefik security ---

    def _check_traefik(self):
        traefik_dir = SERVER_CONTEXT["traefik_config"]
        custom_dir = traefik_dir / "custom"

        if not traefik_dir.is_dir():
            self._pass("Traefik: config directory not found (not applicable)")
            return

        issues = []

        # Parse dynamic-rules.yaml for routes missing auth
        dynamic_rules = custom_dir / "dynamic-rules.yaml"
        if dynamic_rules.exists():
            try:
                config = yaml.safe_load(dynamic_rules.read_text())
                routers = config.get("http", {}).get("routers", {})
                for name, router in routers.items():
                    middlewares = router.get("middlewares", [])
                    rule = router.get("rule", "")
                    # Skip routes pointing to Host(`null`) — disabled routes
                    if "null" in rule:
                        continue
                    has_auth = any("auth" in m.lower() for m in middlewares)
                    if not has_auth:
                        issues.append(f"Route '{name}' has no auth middleware (rule: {rule})")
            except Exception:
                issues.append("Could not parse dynamic-rules.yaml")

        # Verify dashboard has common-auth
        if dynamic_rules.exists():
            try:
                config = yaml.safe_load(dynamic_rules.read_text())
                routers = config.get("http", {}).get("routers", {})
                for name, router in routers.items():
                    if "traefik" in name.lower():
                        middlewares = router.get("middlewares", [])
                        if not any("auth" in m.lower() for m in middlewares):
                            issues.append("Traefik dashboard route missing auth middleware")
            except Exception:
                pass

        # Verify TLS min version
        tls_config = custom_dir / "tls.yaml"
        if tls_config.exists():
            try:
                tls_data = yaml.safe_load(tls_config.read_text())
                min_ver = tls_data.get("tls", {}).get("options", {}).get("default", {}).get("minVersion", "")
                if min_ver not in ("VersionTLS12", "VersionTLS13"):
                    issues.append(f"TLS minimum version is '{min_ver}' — should be VersionTLS12 or higher")
                sni_strict = tls_data.get("tls", {}).get("options", {}).get("default", {}).get("sniStrict", False)
                if not sni_strict:
                    issues.append("TLS sniStrict is not enabled")
            except Exception:
                issues.append("Could not parse tls.yaml")
        else:
            issues.append("No TLS config found at traefik/custom/tls.yaml")

        # Check acme.json permissions
        acme_json = traefik_dir / "acme.json"
        if acme_json.exists():
            mode = oct(acme_json.stat().st_mode)[-4:]
            if int(mode, 8) > 0o600:
                issues.append(f"acme.json has mode {mode} (should be 0600) — contains private keys")

        # Check htpasswd file
        http_auth = traefik_dir / "http_auth"
        if http_auth.exists():
            content = http_auth.read_text().strip()
            if not content:
                issues.append("http_auth (htpasswd) file is empty — basic auth will fail")
        else:
            issues.append("http_auth (htpasswd) file not found — basic auth cannot work")

        if issues:
            severity = "High" if any("no auth" in i.lower() or "dashboard" in i.lower() or "acme.json" in i.lower() for i in issues) else "Medium"
            self._finding(
                severity=severity,
                check="Traefik security",
                detail="; ".join(issues),
                context="Traefik is the public-facing reverse proxy — misconfigured routes or weak TLS expose services to the internet",
                risk="Unauthenticated access to admin panels, weak encryption, or leaked private keys",
                impact="Adding auth middleware may require credentials setup; TLS changes may affect older clients",
            )
        else:
            self._pass("Traefik: all routes authenticated, TLS 1.2+ enforced, sniStrict enabled, acme.json secured")

    # --- Check 14: Tailscale & network segmentation ---

    def _check_tailscale_network(self):
        issues = []

        # Verify Tailscale is running
        ts_status = _run(["tailscale", "status"])
        if ts_status.returncode != 0:
            self._finding(
                severity="High",
                check="Tailscale",
                detail="Tailscale is not running or not installed",
                context="Tailscale provides secure private access to services that shouldn't be public — without it, they're unreachable or must be exposed publicly",
                risk="Loss of secure private access path; may tempt opening services to the public internet",
                impact="Starting Tailscale restores private access; no impact on public services",
                fix_commands=["sudo systemctl start tailscaled", "sudo tailscale up"],
            )
            return

        # Verify UFW is active
        ufw = _run(["sudo", "ufw", "status"])
        if ufw.returncode != 0 or "inactive" in ufw.stdout.lower():
            issues.append("UFW is inactive — direct-bound container ports are publicly reachable")

        # Verify UFW allows tailscale0
        if ufw.returncode == 0:
            ufw_text = ufw.stdout
            if "tailscale0" not in ufw_text and SERVER_CONTEXT["tailscale_interface"] not in ufw_text:
                issues.append(f"UFW has no explicit allow rule for {SERVER_CONTEXT['tailscale_interface']} interface")

            # Check if any direct-bound container port has an explicit UFW allow
            # Get container ports
            docker_ps = _run(["docker", "ps", "--format", "{{.Ports}}"])
            if docker_ps.returncode == 0:
                container_ports = set()
                for line in docker_ps.stdout.strip().splitlines():
                    for port_mapping in line.split(","):
                        port_mapping = port_mapping.strip()
                        if "->" in port_mapping:
                            host_part = port_mapping.split("->")[0]
                            port = host_part.rsplit(":", 1)[-1].split("/")[0]
                            if port not in SERVER_CONTEXT["expected_public_ports"]:
                                container_ports.add(port)

                for port in container_ports:
                    # Check if this port has a UFW allow rule (not on tailscale0)
                    ufw_lines = ufw_text.splitlines()
                    for l in ufw_lines:
                        if port in l and "ALLOW" in l and "tailscale0" not in l and "Anywhere" in l:
                            issues.append(f"Container port {port} has explicit UFW allow from Anywhere — should be Tailscale-only")

        if issues:
            self._finding(
                severity="High" if "inactive" in str(issues) else "Medium",
                check="Tailscale & network segmentation",
                detail="; ".join(issues),
                context="Network segmentation relies on UFW blocking public access to direct-bound container ports, with Tailscale as the private access path",
                risk="Without UFW or with broad allow rules, internal services become publicly accessible",
                impact="Adjusting UFW rules may temporarily disrupt access to affected services",
            )
        else:
            self._pass("Tailscale & network: Tailscale running, UFW active with tailscale0 allowed, no direct-bound ports publicly exposed")

    # --- Check 15: Public route auth audit ---

    def _check_public_route_auth(self):
        dynamic_rules = SERVER_CONTEXT["traefik_config"] / "custom" / "dynamic-rules.yaml"
        if not dynamic_rules.exists():
            self._pass("Public route auth: no Traefik dynamic-rules.yaml found")
            return

        try:
            config = yaml.safe_load(dynamic_rules.read_text())
        except Exception:
            self._finding(
                severity="Medium",
                check="Public route auth audit",
                detail="Could not parse dynamic-rules.yaml",
                context="Cannot verify authentication on public routes",
                risk="Unknown auth state on public-facing services",
                impact="Manual review required",
            )
            return

        routers = config.get("http", {}).get("routers", {})
        no_auth = []
        app_auth_only = []
        traefik_auth = []

        for name, router in routers.items():
            rule = router.get("rule", "")
            # Skip disabled routes (Host(`null`))
            if "null" in rule:
                continue

            middlewares = router.get("middlewares", [])
            has_traefik_auth = any("auth" in m.lower() for m in middlewares)

            # Extract service name from router name
            service_name = name.rsplit("-", 1)[0] if "-" in name else name

            if has_traefik_auth:
                traefik_auth.append(f"{name} ({rule})")
            elif service_name in SERVER_CONTEXT["services_with_own_auth"]:
                app_auth_only.append(f"{name} ({rule}) — app-level auth only ({service_name})")
            else:
                no_auth.append(f"{name} ({rule})")

        issues = []
        if no_auth:
            issues.append(f"Routes with NO auth: {'; '.join(no_auth)}")
        if app_auth_only:
            issues.append(f"Routes with app-level auth only (no Traefik middleware): {'; '.join(app_auth_only)}")

        if no_auth:
            self._finding(
                severity="High",
                check="Public route auth audit",
                detail="; ".join(issues),
                context="Public routes without authentication are accessible to anyone on the internet",
                risk="Unauthorized access to services; data exposure or abuse",
                impact="Adding auth middleware requires setting up credentials and may disrupt existing access",
            )
        elif app_auth_only:
            self._finding(
                severity="Low",
                check="Public route auth audit",
                detail="; ".join(issues),
                context="These services handle their own authentication — Traefik doesn't add a second layer. This is acceptable if the app's auth is robust",
                risk="If app-level auth has vulnerabilities, there's no Traefik fallback",
                impact="Informational — adding Traefik auth would create double-login; usually not worth it",
            )
        else:
            self._pass("Public route auth: all routes have Traefik auth middleware")

        # Report well-protected routes
        if traefik_auth and not no_auth:
            self._pass(f"Public route auth: {len(traefik_auth)} route(s) with Traefik auth middleware")

    # --- Check 16: Git pre-push secret scan ---

    def _check_git_secrets(self):
        git_base = Path.home() / "git"
        if not git_base.is_dir():
            self._pass("Git secret scan: ~/git/ directory not found")
            return

        secret_patterns = [
            (r"(?:API_KEY|SECRET_KEY|SECRET|TOKEN|PASSWORD|PRIVATE_KEY|AUTH_TOKEN)\s*[=:]\s*['\"]?\S{8,}", "API key/secret/token"),
            (r"sk-[a-zA-Z0-9]{20,}", "OpenAI/Stripe secret key"),
            (r"ghp_[a-zA-Z0-9]{36}", "GitHub personal access token"),
            (r"-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----", "Private key"),
            (r"ANTHROPIC_API_KEY\s*=\s*\S+", "Anthropic API key"),
            (r"\b100\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", "Tailscale IP address"),
            (r"\bhtpasswd\b.*\$apr1\$", "htpasswd hash"),
        ]

        sensitive_gitignore_entries = [
            ".env", "credentials.json", "acme.json", "http_auth",
            "agents.db", "security-audit-*.md",
        ]

        issues = []

        for repo_dir in git_base.iterdir():
            if not (repo_dir / ".git").is_dir():
                continue

            repo_name = repo_dir.name

            # Check if repo has a public remote
            remotes = _run(["git", "-C", str(repo_dir), "remote", "-v"])
            if remotes.returncode != 0:
                continue
            has_public = any("github.com" in l for l in remotes.stdout.splitlines())
            if not has_public:
                continue

            # Scan unpushed commits
            unpushed = _run(["git", "-C", str(repo_dir), "log", "@{u}..HEAD", "-p", "--diff-filter=ACMR"])
            diff_text = unpushed.stdout if unpushed.returncode == 0 else ""

            # Also scan staged changes
            staged = _run(["git", "-C", str(repo_dir), "diff", "--cached"])
            diff_text += "\n" + (staged.stdout if staged.returncode == 0 else "")

            if diff_text.strip():
                for pattern, desc in secret_patterns:
                    matches = re.findall(pattern, diff_text)
                    if matches:
                        # Filter out false positives in comments/docs
                        sample = matches[0][:60] + "..." if len(matches[0]) > 60 else matches[0]
                        issues.append(f"[{repo_name}] {desc} found in unpushed/staged changes: {sample}")

            # Verify .gitignore covers sensitive files
            gitignore = repo_dir / ".gitignore"
            if gitignore.exists():
                gitignore_content = gitignore.read_text()
                for entry in sensitive_gitignore_entries:
                    # Check if pattern is covered (exact match or glob)
                    base_name = entry.replace("*", "")
                    if entry not in gitignore_content and base_name not in gitignore_content:
                        # Verify the file actually exists in the repo before flagging
                        if entry.startswith("*"):
                            # Glob pattern — skip existence check
                            pass
                        else:
                            check_file = repo_dir / entry
                            if not check_file.exists():
                                continue
                        issues.append(f"[{repo_name}] .gitignore missing entry for '{entry}'")
            else:
                issues.append(f"[{repo_name}] No .gitignore file — sensitive files may be committed")

        if issues:
            self._finding(
                severity="Critical",
                check="Git pre-push secret scan",
                detail="; ".join(issues),
                context="These repos push to public GitHub — anything pushed is permanently visible, even if force-pushed away later",
                risk="Leaked API keys, private keys, IPs, or infrastructure details in public repositories",
                impact="Secrets found in unpushed commits can be removed with interactive rebase before pushing; already-pushed secrets require rotation",
            )
        else:
            self._pass("Git secret scan: no secrets found in unpushed changes across public repos")

    # --- Check 17: Cloudflare IP range validation (web source) ---

    def _check_cloudflare_ips(self):
        """Fetch official Cloudflare IP ranges and validate UFW rules."""
        try:
            req = urllib.request.Request("https://api.cloudflare.com/client/v4/ips")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
        except (urllib.error.URLError, OSError, json.JSONDecodeError):
            self._pass("Cloudflare IPs: could not fetch ranges (network unavailable)")
            return

        if not data.get("success"):
            self._pass("Cloudflare IPs: API returned error")
            return

        cf_ipv4 = data["result"]["ipv4_cidrs"]
        cf_ipv6 = data["result"]["ipv6_cidrs"]

        # Get UFW rules
        ufw = _run(["sudo", "ufw", "status", "numbered"])
        if ufw.returncode != 0:
            return  # UFW not available — covered by firewall check

        lines = ufw.stdout.splitlines()
        http_anywhere = []
        http_restricted = []

        for line in lines:
            # Look for 80 or 443 rules
            if not any(p in line for p in ("80", "443")):
                continue
            if "ALLOW" not in line:
                continue
            if "Anywhere" in line:
                http_anywhere.append(line.strip())
            else:
                http_restricted.append(line.strip())

        if not http_anywhere and not http_restricted:
            self._pass("Cloudflare IPs: no HTTP/HTTPS UFW rules to validate")
            return

        if http_anywhere:
            cf_ranges_str = ", ".join(cf_ipv4[:5]) + f" (+ {len(cf_ipv4) - 5 + len(cf_ipv6)} more)"
            self._finding(
                severity="Medium",
                check="Cloudflare IP validation",
                detail=f"Ports 80/443 allow Anywhere — should be restricted to Cloudflare ranges: {cf_ranges_str}",
                context="Current Cloudflare IPv4 ranges: " + ", ".join(cf_ipv4),
                risk="Direct access bypasses Cloudflare DDoS protection and WAF rules",
                impact="Restricting to Cloudflare IPs means direct IP access stops working (intended behavior)",
                fix_commands=[
                    "# Replace broad 80/443 rules with Cloudflare-only rules:",
                    "sudo ufw delete allow 80/tcp",
                    "sudo ufw delete allow 443/tcp",
                ] + [f"sudo ufw allow from {cidr} to any port 80,443 proto tcp" for cidr in cf_ipv4],
            )
        else:
            self._pass(f"Cloudflare IPs: HTTP/HTTPS rules restricted to specific ranges ({len(http_restricted)} rules)")

    # --- Check 18: Shodan InternetDB exposure (web source) ---

    def _check_shodan_exposure(self):
        """Check what Shodan's InternetDB sees for our public IP."""
        # Get public IP
        try:
            req = urllib.request.Request("https://api.ipify.org?format=json")
            with urllib.request.urlopen(req, timeout=10) as resp:
                ip_data = json.loads(resp.read().decode())
            public_ip = ip_data["ip"]
        except (urllib.error.URLError, OSError, json.JSONDecodeError, KeyError):
            self._pass("External exposure: could not determine public IP (network unavailable)")
            return

        # Query Shodan InternetDB (free, no auth)
        try:
            req = urllib.request.Request(f"https://internetdb.shodan.io/{public_ip}")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                self._pass("External exposure: server not found in Shodan (good — low profile)")
                return
            self._pass("External exposure: could not query Shodan InternetDB")
            return
        except (urllib.error.URLError, OSError, json.JSONDecodeError):
            self._pass("External exposure: could not query Shodan InternetDB")
            return

        open_ports = set(data.get("ports", []))
        vulns = data.get("vulns", [])
        hostnames = data.get("hostnames", [])

        issues = []
        severity = "Medium"

        # Check for known CVEs — always critical
        if vulns:
            cve_list = ", ".join(vulns[:10])
            if len(vulns) > 10:
                cve_list += f" (+ {len(vulns) - 10} more)"
            issues.append(f"Shodan reports {len(vulns)} known CVE(s): {cve_list}")
            severity = "Critical"

        # Check for unexpected ports
        expected = {80, 443}
        unexpected = open_ports - expected
        if unexpected:
            # SSH externally visible is always high
            if 22 in unexpected:
                issues.append(f"SSH (port 22) is visible externally — should be Tailscale-only")
                if severity != "Critical":
                    severity = "High"
            other = unexpected - {22}
            if other:
                issues.append(f"Unexpected ports visible externally: {sorted(other)}")
                if severity not in ("Critical", "High"):
                    severity = "Medium"

        if issues:
            self._finding(
                severity=severity,
                check="Shodan InternetDB exposure",
                detail="; ".join(issues),
                context=f"Shodan sees this server (IP: {public_ip}) with ports {sorted(open_ports)}"
                        + (f", hostnames: {', '.join(hostnames)}" if hostnames else ""),
                risk="Externally visible ports and CVEs are what attackers scan for — this is the attacker's view of your server",
                impact="Unexpected ports should be blocked at UFW; CVEs require patching the affected service",
            )
        else:
            self._pass(f"External exposure: Shodan sees only expected ports (80, 443) for {public_ip}")

    # --- Interactive fix mode ---

    def run_fix_mode(self):
        """Load the latest audit report and interactively apply fixes."""
        output_dir = REPO_ROOT / "output"
        reports = sorted(output_dir.glob("security-audit-*.md"), reverse=True)
        if not reports:
            print("No audit reports found. Run the audit first: python3 -m agents security-audit")
            return

        report_path = reports[0]
        print(f"Loading: {report_path.name}\n")

        # Re-run checks to get structured findings
        self.findings = []
        self.passed = []
        for step in self.steps():
            try:
                step["fn"]()
            except Exception as e:
                print(f"Warning: check '{step['name']}' failed: {e}", file=sys.stderr)

        fixable = [f for f in self.findings if f.get("fix_commands")]
        if not fixable:
            print("No auto-fixable findings.")
            return

        applied = []
        skipped = []

        for f in fixable:
            print(f"\n{'='*60}")
            print(f"[{f['severity']}] {f['check']}")
            print(f"{'='*60}")
            print(f"\nDetail: {f['detail']}")
            print(f"Context: {f['context']}")
            print(f"Risk: {f['risk']}")
            print(f"Impact: {f['impact']}")
            print(f"\nCommands to run:")
            for cmd in f["fix_commands"]:
                print(f"  $ {cmd}")

            try:
                answer = input(f"\nApply this fix? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\nAborted.")
                return

            if answer == "y":
                for cmd in f["fix_commands"]:
                    print(f"  Running: {cmd}")
                    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
                    if result.returncode != 0:
                        print(f"  FAILED: {result.stderr.strip()}")
                    else:
                        print(f"  OK")
                applied.append(f["check"])
            else:
                skipped.append(f["check"])

        # Summary
        print(f"\n{'='*60}")
        print("Fix Summary")
        print(f"{'='*60}")
        if applied:
            print(f"\nApplied ({len(applied)}):")
            for a in applied:
                print(f"  + {a}")
        if skipped:
            print(f"\nSkipped ({len(skipped)}):")
            for s in skipped:
                print(f"  - {s}")
