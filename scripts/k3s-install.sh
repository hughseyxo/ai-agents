#!/usr/bin/env bash
# scripts/k3s-install.sh — installs k3s bound to the Tailscale interface only.
# Reviewed 2026-08-02 as part of Phase 2 of the k3s migration
# (docs/superpowers/specs/2026-08-01-k3s-migration-design.md, design
# decisions 1-2). Do not run without having read this file first — it
# installs a cluster-admin API on this host.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  echo "no .env file found — TAILSCALE_IP is required" >&2
  exit 1
fi
set -a
source .env
set +a
if [ -z "${TAILSCALE_IP:-}" ]; then
  echo "TAILSCALE_IP not set in .env" >&2
  exit 1
fi

echo "Installing k3s, bound to ${TAILSCALE_IP} only."
echo "Traefik and servicelb disabled (host's own Traefik owns 80/443)."

curl -sfL https://get.k3s.io | INSTALL_K3S_CHANNEL=stable sh -s - server \
  --node-ip="${TAILSCALE_IP}" \
  --bind-address="${TAILSCALE_IP}" \
  --advertise-address="${TAILSCALE_IP}" \
  --flannel-iface=tailscale0 \
  --kube-proxy-arg=nodeport-addresses="${TAILSCALE_IP}/32" \
  --write-kubeconfig-mode 600 \
  --disable=traefik \
  --disable=servicelb

echo "Waiting for node object to register..."
for i in $(seq 1 30); do
  if sudo k3s kubectl get nodes --no-headers 2>/dev/null | grep -q .; then
    break
  fi
  sleep 2
done
echo "Waiting for node Ready..."
sudo k3s kubectl wait --for=condition=Ready node --all --timeout=120s
sudo k3s kubectl get nodes -o wide
