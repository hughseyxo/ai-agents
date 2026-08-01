# Obsidian Vault — Device Setup

CouchDB + livesync-bridge run in the yopflix seedbox stack (started by `run-seedbox.sh`),
Tailscale-only (`<TAILSCALE_IP>:5984`). Agents keep writing plain `.md`; the bridge mirrors
disk ⇄ CouchDB, and Obsidian clients sync against CouchDB.

`<TAILSCALE_IP>` is this host's Tailscale IP, kept out of tracked files — see `TAILSCALE_IP`
in `.env` (gitignored, in the `ai-agents` repo root), or run `tailscale ip -4` on the host.

## Credential model (how it actually wires up)

The seedbox injects per-service secrets from **`~/git/yopflix/seedbox/.env.custom`**
(gitignored) using `APPNAME_`-prefixed keys. `run-seedbox.sh` strips the prefix into
`env/<service>.env` and attaches it to the container via a generated `env_file:` override.
There is **no** secret material in any tracked file.

`.env.custom` already contains (replace the password with your own strong value):

```
# CouchDB admin — entrypoint sets this admin on first boot
COUCHDB_COUCHDB_USER=admin
COUCHDB_COUCHDB_PASSWORD=<strong-password>

# Bridge connects to CouchDB with the same creds
LIVESYNC-BRIDGE_COUCHDB_USER=admin
LIVESYNC-BRIDGE_COUCHDB_PASSWORD=<strong-password>
```

- `services/livesync-bridge/config.json` keeps `${COUCHDB_USER}` / `${COUCHDB_PASSWORD}`
  literal placeholders — the bridge substitutes them at runtime from its own env
  (`src/main.ts`), so they are filled from the `LIVESYNC-BRIDGE_` keys above.
- **Do not** add `COUCHDB_USER=${COUCHDB_USER}` to the `environment:` block of
  `livesync-bridge.yaml`. That interpolates from the compose `--env-file` (`.env`, which
  has no COUCHDB keys) and overrides the working `env_file` value with an empty string,
  breaking auth on a clean restart.
- `services/couchdb/local.ini` holds only non-secret config (single_node, CORS,
  require_valid_user, uuid). The admin lands in `services/couchdb/docker.ini`, which the
  CouchDB entrypoint generates from the env at boot and is **gitignored**.

## Start

The stack is run as root (traefik/container-owned files):

```bash
cd ~/git/yopflix/seedbox && sudo ./run-seedbox.sh --no-pull
sudo ss -tlnp | grep 5984      # MUST show <TAILSCALE_IP>:5984, never 0.0.0.0
```

No `_cluster_setup` call is needed — `single_node=true` is preset in `local.ini`, and the
`obsidian-vault` database already exists in the data volume. (To create it from scratch:
`curl -X PUT http://<TAILSCALE_IP>:5984/obsidian-vault -u admin:PASS`.)

## Obsidian device setup (phone/PC on Tailscale)

1. Install Obsidian + **Self-hosted LiveSync** community plugin.
2. Remote Database settings:
   - URI: `http://<TAILSCALE_IP>:5984`
   - Username/Password: as set in `.env.custom`
   - Database name: `obsidian-vault`
3. Initial sync direction: **Remote → Local**.
4. Quick-setup guide: https://github.com/vrtmrz/obsidian-livesync/blob/main/docs/quickstart.md

## Round-trip verification

```bash
echo "sync test" > docs/daily/_synctest.md
# wait ~30s, then check CouchDB:
curl -s "http://<TAILSCALE_IP>:5984/obsidian-vault/_all_docs" -u admin:PASS | grep synctest
rm docs/daily/_synctest.md
```

Create a note on phone → confirm it appears under `docs/` on disk within ~30s.

## Known behaviour — deletions

Creates and edits propagate **disk ⇄ CouchDB within ~30s** (chokidar watch). **On-disk
deletions do not propagate live** — the bridge reconciles removals on its offline scan
(container restart). To force a deletion through immediately, either restart the bridge
(`sudo docker restart livesync-bridge`) or delete the doc directly in CouchDB:

```bash
rev=$(curl -s -u admin:PASS "http://<TAILSCALE_IP>:5984/obsidian-vault/<docid>" | jq -r ._rev)
curl -s -X DELETE -u admin:PASS "http://<TAILSCALE_IP>:5984/obsidian-vault/<docid>?rev=$rev"
```

`<docid>` is the path relative to the peer baseDir (e.g. `daily/2026-06-21.md` for the
docs root, `_memory/...` for memory, `_project/CLAUDE.md` for the project root).
