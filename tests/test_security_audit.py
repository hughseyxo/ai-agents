"""Tests for web-based security audit checks."""

import json
import urllib.error
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from agents.security_audit import SecurityAuditAgent


@pytest.fixture
def agent():
    with patch.object(SecurityAuditAgent, '__init__', lambda self, **kw: None):
        a = SecurityAuditAgent.__new__(SecurityAuditAgent)
        a.findings = []
        a.passed = []
        a.context = {}
        a._failed_steps = []
        return a


# --- Cloudflare IP validation ---

CLOUDFLARE_RESPONSE = {
    "success": True,
    "result": {
        "ipv4_cidrs": [
            "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22",
            "104.16.0.0/13", "108.162.192.0/18", "131.0.72.0/22",
            "141.101.64.0/18", "162.158.0.0/15", "172.64.0.0/13",
            "190.93.240.0/20", "188.114.96.0/20", "197.234.240.0/22",
            "198.41.128.0/17",
        ],
        "ipv6_cidrs": [
            "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32",
            "2405:b500::/32", "2405:8100::/32", "2a06:98c0::/29",
            "2c0f:f248::/32",
        ],
    },
}


class TestCloudflareIPs:
    def test_reports_ufw_anywhere_rules(self, agent):
        """When 80/443 allow Anywhere, flag with CF ranges for reference."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(CLOUDFLARE_RESPONSE).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        ufw_output = (
            "Status: active\n"
            "     To                         Action      From\n"
            "     --                         ------      ----\n"
            "[ 1] 80/tcp                     ALLOW IN    Anywhere\n"
            "[ 2] 443/tcp                    ALLOW IN    Anywhere\n"
            "[ 3] 22/tcp                     ALLOW IN    Anywhere\n"
        )

        with patch("urllib.request.urlopen", return_value=mock_resp), \
             patch("agents.security_audit._run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=ufw_output)
            agent._check_cloudflare_ips()

        assert len(agent.findings) == 1
        assert agent.findings[0]["severity"] == "Medium"
        assert "Cloudflare" in agent.findings[0]["check"]
        assert "173.245.48.0/20" in agent.findings[0]["detail"]

    def test_passes_when_no_http_rules(self, agent):
        """No 80/443 rules in UFW — nothing to validate."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(CLOUDFLARE_RESPONSE).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        ufw_output = (
            "Status: active\n"
            "     To                         Action      From\n"
            "     --                         ------      ----\n"
            "[ 1] 22/tcp                     ALLOW IN    Anywhere\n"
        )

        with patch("urllib.request.urlopen", return_value=mock_resp), \
             patch("agents.security_audit._run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=ufw_output)
            agent._check_cloudflare_ips()

        assert len(agent.findings) == 0
        assert any("Cloudflare" in p for p in agent.passed)

    def test_passes_when_restricted_to_cf_ranges(self, agent):
        """UFW rules already restrict to Cloudflare CIDRs — pass."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(CLOUDFLARE_RESPONSE).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        ufw_output = (
            "Status: active\n"
            "     To                         Action      From\n"
            "     --                         ------      ----\n"
            "[ 1] 80/tcp                     ALLOW IN    173.245.48.0/20\n"
            "[ 2] 443/tcp                    ALLOW IN    104.16.0.0/13\n"
        )

        with patch("urllib.request.urlopen", return_value=mock_resp), \
             patch("agents.security_audit._run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=ufw_output)
            agent._check_cloudflare_ips()

        assert len(agent.findings) == 0
        assert any("Cloudflare" in p for p in agent.passed)

    def test_graceful_on_network_failure(self, agent):
        """Network down — skip check, don't crash."""
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timeout")):
            agent._check_cloudflare_ips()

        assert len(agent.findings) == 0
        assert any("Cloudflare" in p for p in agent.passed)

    def test_graceful_on_api_error(self, agent):
        """CF API returns error — skip check."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"success": False}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            agent._check_cloudflare_ips()

        assert len(agent.findings) == 0
        assert any("Cloudflare" in p for p in agent.passed)

    def test_ufw_unavailable(self, agent):
        """UFW command fails — skip (covered by firewall check)."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(CLOUDFLARE_RESPONSE).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp), \
             patch("agents.security_audit._run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            agent._check_cloudflare_ips()

        assert len(agent.findings) == 0


# --- Shodan InternetDB exposure ---

SHODAN_RESPONSE = {
    "cpes": [],
    "hostnames": ["example.com"],
    "ip": "1.2.3.4",
    "ports": [80, 443],
    "tags": [],
    "vulns": [],
}


class TestShodanExposure:
    def _mock_urlopen(self, responses):
        """Helper: returns different responses for different URLs."""
        def side_effect(req, **kwargs):
            url = req.full_url if hasattr(req, 'full_url') else req
            for pattern, resp_data in responses:
                if pattern in url:
                    mock = MagicMock()
                    mock.read.return_value = json.dumps(resp_data).encode()
                    mock.__enter__ = lambda s: s
                    mock.__exit__ = MagicMock(return_value=False)
                    return mock
            raise urllib.error.URLError("no mock")
        return side_effect

    def test_clean_scan_passes(self, agent):
        """Only 80/443 open, no vulns — pass."""
        responses = [
            ("ipify", {"ip": "1.2.3.4"}),
            ("internetdb", SHODAN_RESPONSE),
        ]
        with patch("urllib.request.urlopen", side_effect=self._mock_urlopen(responses)):
            agent._check_shodan_exposure()

        assert len(agent.findings) == 0
        assert any("Shodan" in p or "exposure" in p.lower() for p in agent.passed)

    def test_unexpected_ports_flagged(self, agent):
        """Ports beyond 80/443 visible externally — finding."""
        shodan = {**SHODAN_RESPONSE, "ports": [22, 80, 443, 8080, 9696]}
        responses = [
            ("ipify", {"ip": "1.2.3.4"}),
            ("internetdb", shodan),
        ]
        with patch("urllib.request.urlopen", side_effect=self._mock_urlopen(responses)):
            agent._check_shodan_exposure()

        assert len(agent.findings) == 1
        assert agent.findings[0]["severity"] in ("High", "Medium")
        assert "22" in agent.findings[0]["detail"] or "8080" in agent.findings[0]["detail"]

    def test_known_cves_flagged(self, agent):
        """Shodan reports CVEs — critical finding."""
        shodan = {**SHODAN_RESPONSE, "vulns": ["CVE-2024-1234", "CVE-2024-5678"]}
        responses = [
            ("ipify", {"ip": "1.2.3.4"}),
            ("internetdb", shodan),
        ]
        with patch("urllib.request.urlopen", side_effect=self._mock_urlopen(responses)):
            agent._check_shodan_exposure()

        assert len(agent.findings) >= 1
        cve_finding = [f for f in agent.findings if "CVE" in f["detail"]]
        assert len(cve_finding) == 1
        assert cve_finding[0]["severity"] == "Critical"

    def test_not_found_in_shodan_passes(self, agent):
        """IP not in Shodan — good, low profile."""
        def side_effect(req, **kwargs):
            url = req.full_url if hasattr(req, 'full_url') else req
            if "ipify" in url:
                mock = MagicMock()
                mock.read.return_value = json.dumps({"ip": "1.2.3.4"}).encode()
                mock.__enter__ = lambda s: s
                mock.__exit__ = MagicMock(return_value=False)
                return mock
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

        with patch("urllib.request.urlopen", side_effect=side_effect):
            agent._check_shodan_exposure()

        assert len(agent.findings) == 0
        assert any("not found" in p.lower() or "low profile" in p.lower() for p in agent.passed)

    def test_network_failure_graceful(self, agent):
        """Can't reach ipify — skip check."""
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timeout")):
            agent._check_shodan_exposure()

        assert len(agent.findings) == 0
        assert any("exposure" in p.lower() or "public IP" in p.lower() for p in agent.passed)

    def test_only_expected_ports_with_hostnames(self, agent):
        """Expected ports + hostnames — include hostnames in pass message."""
        shodan = {**SHODAN_RESPONSE, "hostnames": ["media.example.com", "home.example.com"]}
        responses = [
            ("ipify", {"ip": "1.2.3.4"}),
            ("internetdb", shodan),
        ]
        with patch("urllib.request.urlopen", side_effect=self._mock_urlopen(responses)):
            agent._check_shodan_exposure()

        assert len(agent.findings) == 0

    def test_ssh_exposed_is_high_severity(self, agent):
        """SSH visible externally is always high severity."""
        shodan = {**SHODAN_RESPONSE, "ports": [22, 80, 443]}
        responses = [
            ("ipify", {"ip": "1.2.3.4"}),
            ("internetdb", shodan),
        ]
        with patch("urllib.request.urlopen", side_effect=self._mock_urlopen(responses)):
            agent._check_shodan_exposure()

        assert len(agent.findings) == 1
        assert agent.findings[0]["severity"] == "High"
        assert "22" in agent.findings[0]["detail"]
