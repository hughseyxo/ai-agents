#!/usr/bin/env bash
# scripts/k3s-firewall-apply.sh — scoped UFW route rules for k3s (design
# decision 2). Adds exactly two allow rules: pod CIDR -> apiserver, pod
# CIDR -> cluster DNS. Does NOT touch any existing UFW rule. Uses `ufw
# route`, the mechanism for FORWARD-chain (routed) traffic, not a bare
# `ufw allow` (which only governs the INPUT chain).
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

sudo ufw route allow from "${POD_CIDR}" to "${TAILSCALE_IP}" port 6443 proto tcp comment 'k3s: pods to apiserver'
sudo ufw route allow from "${POD_CIDR}" to "${CLUSTER_DNS}" port 53 proto udp comment 'k3s: pods to coredns udp'
sudo ufw route allow from "${POD_CIDR}" to "${CLUSTER_DNS}" port 53 proto tcp comment 'k3s: pods to coredns tcp'

echo "Applied. Current routed rules matching pod CIDR:"
sudo ufw status verbose | grep "${POD_CIDR}" || echo "(none matched — check ufw status manually)"
