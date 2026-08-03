#!/usr/bin/env bash
# scripts/k3s-firewall-rollback.sh — removes exactly the rules
# scripts/k3s-firewall-apply.sh added. Safe to run even if the rules were
# never applied (ufw route delete on a non-existent rule is a no-op error,
# caught here so the script doesn't abort partway through).
set -euo pipefail
cd "$(dirname "$0")/.."
set -a
source .env
set +a
if [ -z "${TAILSCALE_IP:-}" ]; then
  echo "TAILSCALE_IP not set in .env" >&2
  exit 1
fi

POD_CIDR="10.42.0.0/16"
CLUSTER_DNS="10.43.0.10"

sudo ufw route delete allow from "${POD_CIDR}" to "${TAILSCALE_IP}" port 6443 proto tcp || true
sudo ufw route delete allow from "${POD_CIDR}" to "${CLUSTER_DNS}" port 53 proto udp || true
sudo ufw route delete allow from "${POD_CIDR}" to "${CLUSTER_DNS}" port 53 proto tcp || true

echo "Rolled back. Confirming no pod-CIDR rules remain:"
sudo ufw status verbose | grep "${POD_CIDR}" && echo "WARNING: rule(s) still present" || echo "clean"
