# Code-Review Remediation Plan

**Source:** full-tree review in `output/code-review/` (batches A–H + SUMMARY), 2026-06-19.
**Scope:** Critical + High + Medium findings across batches A–G. Bridge-server findings
(Batch E Criticals C14–C17 + its Highs/Mediums) are **dropped** — the bridge is being
**deleted** (Phase 0), not hardened. Low/style findings are out of scope.
**Method:** TDD (test first, per project rules); `ecc:python-review` on the diff before
declaring each phase done; pre-push commit-security gate must pass.
**Constraint:** dual-CLI safe (no Claude/Antigravity-specific Python).

Phases are ordered by leverage: Phase 0 deletes dead code (removes the largest risk surface
for free), Phase 1 lands shared root-cause helpers that several later fixes depend on, then
per-area phases. Each phase is independently shippable as one commit.

---

## Phase 0 — Delete the MCP bridge server (bloat + risk removal)

Rationale: the laptop-trigger tool surface (`exec_shell`, `read_file`, `write_file`,
`run_agent`) is no longer used (replaced by Claude remote) and is the entire Batch-E
Critical surface. Only coupling: `librarian.py` emails approve/reject links to its HTTP
endpoints. Replace that with a CLI so the proposal workflow survives serverless.

1. **Stop & remove the service**
   - `systemctl --user disable --now mcp-bridge.service`
   - Delete `mcp-bridge.service`, `~/.config/systemd/user/mcp-bridge.service` (symlink),
     `mcp-servers/.env.bridge`.
2. **Delete code & docs**
   - `mcp-servers/bridge_server.py`
   - `docs/mcp-bridge.md`, `docs/superpowers/plans/2026-05-17-laptop-server-bridge.md`
     (laptop-bridge plan — now obsolete; keep librarian design docs).
   - `triggers/README.md` — rewrite or delete if it only documents bridge-triggering.
3. **Decouple the librarian** (`agents/librarian.py`)
   - Remove `BRIDGE_BASE` (`:47`) and the `MCP_BRIDGE_TOKEN` link-building in
     `_build_html_report` (`:290,338-354`).
   - Replace the email's approve/reject `<a>` buttons with: the proposal id + a one-line
     instruction (`run: python3 -m agents librarian apply <id>` / `… reject <id>`).
   - **New CLI:** add `apply`/`reject` subcommands (in `runner.py` + a `LibrarianAgent`
     method) that load `proposals/<id>.json`, perform the same effect the bridge endpoint
     did (atomic file write via the Phase-1 helper + `git add`/`commit`; status→applied/
     rejected). This is the serverless replacement for the HTTP apply.
   - **Test first:** `tests/test_librarian.py::test_apply_proposal_writes_and_commits`,
     `::test_reject_marks_status`, `::test_apply_unknown_id_errors`,
     `::test_apply_rejects_path_outside_repo` (carry over the bridge's traversal guard).
4. **Scrub references**
   - `run-agent.sh:3` — drop `MCP_BRIDGE_TOKEN` from the export line.
   - `CLAUDE.md` — remove bridge from Project Structure + the mcp-servers description;
     update the librarian line (approve/reject now CLI, not bridge).
   - `README.md` — remove bridge mentions.
   - Update memory if it references the bridge (none currently).
5. **Verify:** `pgrep -af bridge_server` empty; `grep -rI bridge` only hits historical
   design docs (acceptable); librarian audit dry-run still emails + the new CLI applies a
   test proposal.

---

## Phase 1 — Shared root-cause helpers (highest leverage)

These close one finding in many files at once; later phases depend on them.

1. **`safe_profile_path()` — central traversal guard** (closes C2, C11, C19 + librarian/
   concierge traversal)
   - In `agents/plant_profiles.py`: add `safe_profile_path(name) -> Path` that slugifies,
     resolves, and asserts `.is_relative_to(PLANTS_DIR.resolve())`, raising `ValueError`
     on escape. Route `write_health_assessment`, `append_frequency_history`, and
     `append_intelligence_note` through it (the last already has the check — consolidate).
   - Add a generic `assert_within(base, path)` util reused by the PWA, concierge tool, and
     librarian-learnings paths.
   - **Test first:** `tests/test_plant_profiles.py` — `..`, `%2F`, absolute, null-byte,
     unicode-slash names all rejected by all three writers.
2. **`AgentDB` concurrency** (closes A/F Highs at the root)
   - `agents/db.py:62-65`: `PRAGMA journal_mode=WAL`, `PRAGMA busy_timeout=5000`, add a
     `threading.Lock` wrapping every execute+commit; guard `mkdir` errors.
   - **Test first:** `tests/test_db.py` (new) — two threads write distinct keys
     concurrently then read both; corrupt-JSON `get_state` raises contextful `ValueError`.
3. **Reuse atomic writes everywhere** (pattern 3)
   - Confirm `write_profile_atomic` (plant_profiles) + `save_tokens` pattern are the single
     blessed helper; wire librarian learnings/memory/proposal writes (Phase 5),
     `security_audit.report()`, and the PWA/bot profile writes through it.
4. **Logging baseline (Medium, many files)**
   - Add a tiny `agents/log.py` (`getLogger` + stderr handler) or standardise on
     `print(..., file=sys.stderr)` with a `[agent] ` prefix. Replace silent
     `except: pass`/`return []` sites flagged in A/B/C/E/G with a logged warning.
     (Mechanical; fold into each phase rather than one mega-commit.)

---

## Phase 2 — Core framework (Batch A)

- **C1** `plant_model.py:144`: log the swallowed assessment exception (or let it propagate);
  test a corrupt `last_assessment` blob surfaces a warning, not silent `None`.
- **H** `weather.py:42`: **first verify** the Open-Meteo query params (does it request
  `past_days`?). If the slice is taking forecast hours, switch to `past_days=1` + `[:24]`
  or `daily.precipitation_sum[0]`. Test with a synthetic 72-pt series.
- **H** `plant_model.py:110-117`: wrap `update_plant` read-modify-write in the Phase-1 DB
  lock / `BEGIN EXCLUSIVE`; test concurrent `set_plant_frequency` vs tick doesn't clobber.
- **H** `base.py:174-176`: log full stderr when `rc==0 && empty stdout`. `:196-199`:
  `except TimeoutExpired as e: e.process.kill(); e.process.communicate()`.
- **H** `runner.py`: validate `schedule`/`name` with regex before crontab write
  (`:115-116`); distinguish "no crontab" from real `crontab -l` failure, abort instead of
  wiping (`:125-127,152`).
- **M** `db.py:122` contextful JSON error; `base.py:152-156` guard learnings read;
  `plant_weather.py:134-138` `baseline … or 7`; `weather.py:33` narrow except + log lat/lon;
  `db.py:79,86` `str | None`.

## Phase 3 — Scheduled agents (Batch B)

- **C3/C4/C5** `librarian.py`: validate LLM findings (`isinstance list`, required keys,
  `agent` in allowlist, numeric `confidence`) before any apply; wrap both `json.loads`
  (`:198,:422`) in try/except→log+`[]`. (Path-traversal for agent-name folded into Phase-1
  `assert_within`.) Test: malformed JSON → no-op+logged; bad `agent` name rejected;
  string `confidence` rejected.
- **H** `plant_agent.py:27,155`: per-plant try/except around `strptime`; skip+log offender.
- **H** `news_briefing.py:501-503`: capture `synthesize()` return, only `mark_seen` if it's
  a non-error/non-refusal result. Test: refusal string → not marked → retried next run.
- **H** `agent_health.py:152-156`: only `set_state("alerted")` for delivered alerts; check
  `_send_telegram` return for recovery messages too.
- **H** `travel_agent.py:77-98`: `if content is None: return "skipped"` before write.
- **H** `librarian.py:368,431` UTC-aware `datetime`; `:383` `fromisoformat` compare.
- **M** `plant_agent.py:231-236` raise on intelligence parse failure (don't advance gate);
  `news_briefing.py:177-181,224-225` return `warning`/`scoring_fallback` flags;
  `:137-139` narrow except; `:242` copy dict before `pop`; `daily_briefing.py:88` `.get`;
  `travel_agent.py:65-69` return error dict; `:304` `except (ValueError,TypeError)`.

## Phase 4 — Security agents (Batch C)

- **C6** `commit_security.py:37,132`: `f.get("severity","").lower()` in the block
  decision. **Test first** (closes the H-batch latent bug): title-case `"Critical"` →
  `run_hook()==1`.
- **C7** `:37,100`: filter LLM array to dicts before `.get`. Test non-dict item.
- **C8** `security_audit.py:1288`: drop `shell=True`, `shlex.split`; regex-validate CIDRs
  (`:1160`) before interpolation.
- **C9** `:612,618-631`: surface cert uncertainty as a non-PASS; `continue` not `return`.
- **H** `:64,28,44,44-54` gate fail-open policy (warn loudly / fail-closed default),
  `_get_diff` `timeout=60`, top-level guard, validate `GIT_PUSH_RANGE`.
- **H** `:66` delimit untrusted diff in prompt + schema-validate response.
- **H** `:1109-1160` CIDR validation+cap; `:602` tz-aware; `:245-249` parse `ALLOW` per
  line; `:817-827` reuse first-parse config.
- **M** schema-validate findings; sanitise ipify/Shodan into report; `git log -p` size cap;
  acme.json key check; docker-inspect JSON; glob `"*" in entry`; shared traefik loader.

## Phase 5 — Telegram concierge bot (Batch D)

- **C10** `bot.py:427-467`: add `ALLOWED_USER_ID` check to `_handle_callback`. Test:
  foreign user → `update_plant` not called.
- **C11** traversal via plant name → route through Phase-1 `safe_profile_path`.
- **C12** `:352,377`: `await loop.run_in_executor(None, …)` for both photo subprocess calls.
- **C13** `:451`: `update_plant(plant_name, frequency_days=new_days)`.
- **H** `:440-441` `split(":",2)`; `:460-463` use `write_profile_atomic`; expose
  `ask_antigravity_simple` instead of importing `_run_agy`; wrap user text in
  `<user_input>` delimiters (`antigravity_backend.py:71`, `claude_backend.py:107`).
- **H** `tools.py:514` validate URL (https, block private/`--`); `:229-245` argv `--`
  sentinel + date regex; `:109-117` check `docker ps` rc; `:496` add `note_plant_observation`
  to `SPECS` (or delete impl).
- **H** `claude_backend.py:44,88` lock `_SESSIONS`.
- **M** `/start` auth gate; reuse `all_plants`; `Popen.wait()`; pre-fetch weather; strip
  newlines from research plant_name; fix assessment regex; cap profile read; move stdlib
  imports up; delete dead `openai_tools()` + stale OpenRouter docstrings.

## Phase 6 — MCP servers (Batch E, bridge-excluded)

- **C18** `gmail_server.py:96-113`: `get_profile()` → set `From` to authenticated address.
  Test: sent message From == account, not recipient.
- **H** `calendar/gmail_server.py:44-68`: catch `urllib.error.HTTPError`, log body to
  stderr, raise generic `RuntimeError` on OAuth refresh failure.
- **H** `concierge_server.py:56-57` (+cal/gmail): send `-32700` for id-bearing requests on
  `JSONDecodeError`; log to stderr (don't silently `continue`).
- **H** `gmail_server.py:204` register `gmail_create_draft` in `TOOLS`.
- **H** `laptop_auth.py:112-114` & `server_auth.py:82-84`: atomic token write
  (`mkstemp`+`chmod 600`+`os.replace`).
- **M** `concierge_server.py:42` arg size cap; cal/gmail unused imports + GET Content-Type;
  stderr logging on tool errors.

## Phase 7 — Plant PWA (Batch F)

- **C19** route every `profile_path` use (`:226,289,392,413`) through Phase-1
  `safe_profile_path`; strict name `pattern` on `PlantCreate`/`PlantUpdate` + `{name}`
  handlers.
- **C20** `:428-433` don't pass raw profile markdown verbatim — truncate/section it; reject
  control/markdown chars in names at creation.
- **H** `:402` upload: content-type allowlist + magic-byte check + 10 MB cap.
- **H** `:537` add a shared-secret header dependency on all write endpoints (or bind
  `127.0.0.1` behind `tailscale serve`). **Decision needed at impl time** — note in commit.
- **H** `:289-292` bounds-check rename; `:229,416,423` make `upload_photo` sync `def` or
  `to_thread` the reads.
- **M** `%2F` regex guard; `Field` constraints; validate care-task inputs; `response_model`s;
  upload size middleware; lock `save_plants` (Phase-1 DB lock); route-order comment.

## Phase 8 — Skills & misc (Batch G)

- **C21** `prepare_output.py:82-84`: `html.escape()` all external RSS strings; validate link
  scheme. Test: feed entry with `<script>`/`javascript:` is neutralised.
- **C22** `mealsave.py:291,563,590`: `cmd += ["--", url]`.
- **C23** `mealsave.py:351`: validate URL scheme/host before byparr POST (block RFC-1918/
  link-local).
- **H** `mealsave.py:221,491` strip body from `die()`; `:438-455` guarantee dict/`die()`;
  `free_time_bot.py:103` subprocess `timeout`; `:179`/`mealsave_bot.py:114` generic user
  error + server-side log; `mealsave_bot.py:86` async subprocess; `parse_feeds.py:47,59`
  log + narrow excepts.
- **M** tempfile paths; URL regex trailing punct; numeric `TELEGRAM_USER_ID` guard;
  transcribe error visibility; `capture_output` on ffmpeg; `RequestException` base; unused
  imports.

---

## Sequencing & verification
- Commit per phase (atomic). Phase 0 first (removes risk for free), Phase 1 next (unblocks
  2/5/7). Phases 2–8 independent thereafter.
- Each phase: write tests first → implement → `ecc:python-review` on the diff → full
  `pytest` green → pre-push gate passes.
- After all phases: re-run the relevant batch reviewers on the diff to confirm Crit/High
  closed; update `CLAUDE.md` for any structural change (bridge removal, new CLI, log util).

## Out of scope
Bridge hardening (deleted instead); Low/style findings (`E701`/`F541`/unused-imports beyond
the ones bundled above); `print`→`logging` blanket migration (only the silent-failure sites
are touched).
