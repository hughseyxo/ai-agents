# Obsidian Vault — Device Setup

CouchDB + livesync-bridge run in the yopflix seedbox stack (started by `run-seedbox.sh`), Tailscale-only.

## Before first run

1. Set real credentials in `~/git/yopflix/seedbox/.env.custom`:
   ```
   COUCHDB_USER=<admin-username>
   COUCHDB_PASSWORD=<strong-password>
   ```
2. Update `services/livesync-bridge/config.json` — replace `${COUCHDB_USER}` / `${COUCHDB_PASSWORD}` with the same values (or confirm the bridge supports env substitution — verify schema against https://github.com/vrtmrz/livesync-bridge README).

## Start

```bash
cd ~/git/yopflix/seedbox && ./run-seedbox.sh
ss -tlnp | grep 5984   # must show 100.96.86.73:5984, NOT 0.0.0.0
```

## One-time CouchDB init

```bash
# Enable single-node mode
curl -X POST "http://100.96.86.73:5984/_cluster_setup" \
  -H "Content-Type: application/json" -u "USER:PASS" \
  -d '{"action":"enable_single_node","username":"USER","password":"PASS","bind_address":"100.96.86.73","port":5984,"singlenode":true}'

# Create the vault database
curl -X PUT "http://100.96.86.73:5984/obsidian-vault" -u "USER:PASS"
```

## Obsidian device setup (phone/PC on Tailscale)

1. Install Obsidian + **Self-hosted LiveSync** community plugin.
2. Remote Database settings:
   - URI: `http://100.96.86.73:5984`
   - Username/Password: as set above
   - Database name: `obsidian-vault`
3. Initial sync direction: **Remote → Local**.
4. Quick-setup guide: https://github.com/vrtmrz/obsidian-livesync/blob/main/docs/quickstart.md

## Round-trip verification

```bash
echo "sync test" > docs/daily/_synctest.md
# wait ~30s, then check CouchDB:
curl -s "http://100.96.86.73:5984/obsidian-vault/_all_docs" -u "USER:PASS" | grep synctest
rm docs/daily/_synctest.md
```

Create a note on phone → confirm it appears under `docs/` on disk within ~30s.
