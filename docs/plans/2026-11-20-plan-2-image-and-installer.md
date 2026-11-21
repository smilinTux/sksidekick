# Plan 2: container image and plain installer

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** sksidekick design (approved 2026-11-20) (private design doc), sections "What it is", "Two editions",
"Where it runs", "Moving", "Export ... delete". Roadmap and Plan 1: `docs/plans/2026-11-20-plan-1-story-safe-core.md`.

**Goal:** One sidekick runs from one OCI image (Linux x86_64 and arm64) or from a plain installer (macOS Apple Silicon primary, Linux too), with the Plan 1 core keeping its whole story safe and exactly one copy answering.

**Architecture:** All of a sidekick's data lives in ONE directory, the sidekick home (`/opt/data/sidekick` in the image, `~/Library/Application Support/sksidekick` on macOS, `~/.local/share/sksidekick` on Linux). Code lives outside it. A small supervisor, `sksidekick serve`, refuses to start unless the copy is live, then runs the Hermes gateway as a child and runs backups and drills on schedule. Hermes reaches memory only through the `skmemory-mcp` child that hermes-skmemory starts (design: skmemory stays outside Hermes'
venv), and that child keeps its store inside `HERMES_HOME`, so inside the home.

**Tech Stack:** Hermes fork `chefboyrdave21/hermes-agent` tag `v0.23.1-sk.4` (its own pinned Dockerfile and `pm` lock); hermes-skmemory `v0.2.0`; skmemory (local tier only: flat JSON + SQLite/FTS5); sksidekick (Plan 1); Docker buildx; launchd (macOS) and systemd user units (Linux); GitHub Actions (ubuntu arm and x86, macos-14).

## Decisions this plan locks in

1. **Data layout (one home, everything else rebuildable):**
   ```
   <home>/memory/            memories added outside Hermes (import, tools); index.db* excluded from archives (rebuilt)
   <home>/soul/              soul file with the personality type and voice
   <home>/config/            sksidekick config (backup.json, serve.json)
   <home>/journal.md
   <home>/hermes/            HERMES_HOME: config.yaml, sessions, state DB (the conversations are part of the story)
   <home>/hermes/.skmemory/agents/sidekick/   the memory provider's store: memory/, config/ (story; index.db* and logs/ left out)
   <home>/keys/, <home>/state/   Plan 1 (never in an archive except the phrase-wrapped key)
   ```
   Excluded from the story, so never in an archive: `hermes/logs/`, `hermes/cache/`, `hermes/tools/` (pm-installed toolchain), `hermes/plugins/`
   (reinstalled from the image), the store's `logs/`, and any `*.lock` or `*.pid`.
2. **Memory wiring (corrected 2026-11-20 after reading hermes-skmemory v0.2.0):** hermes-skmemory in its default **isolated** mode, agent name
   `sidekick` (`hermes/skmemory.json`: `{"agent": "sidekick", "assume_owner": true}`). Linked mode cannot be used: it strips every inherited
   `SKMEMORY_*` variable and writes under an skcapstone home outside the sidekick home. Isolated mode keeps the store at
   `$HERMES_HOME/.skmemory/agents/sidekick/`, inside the home, so every backup has it. `serve` refuses to start when `HERMES_HOME` is not the home's
   `hermes/` or `skmemory.json` sets `skcapstone_home` (the two ways memory would land where backups miss it).
3. **Secrets in the home:** channel tokens live in `hermes/config.yaml` and `hermes/.env`. They are part of an encrypted archive (the design says
   "credentials only if the user opts in"), so `export` takes `--with-credentials`. Without it, those two files go into the archive with their secret
   values replaced by `__REMOVED__`, and import says which channels need logging in again. Backups always include credentials (they are only ever
   opened by the same user's key).
4. **One live copy:** `serve` calls `lifecycle.require_live` at start and every 5 s, and stops the gateway within 5 s of the status leaving `live`.
   `move-out` therefore silences the old copy before the archive is even copied.
5. **Schedules inside `serve`, not cron:** backup every `interval_hours` (default 24), drill every 7 days, both jittered by up to 10 min. Their
   results go to `state/`, and a failure or a stale backup becomes a message to the user on their own channel (design: alerts only to the user,
   nothing phones home). The message text is fixed in Task 3.
6. **Image:** `ghcr.io/smilintux/sksidekick:<version>` (private until Chef says public), multi-arch, built FROM the Hermes fork's own Dockerfile
   at the pinned tag (built in the same workflow as stage `hermes`), plus skmemory and hermes-skmemory at pinned versions, plus sksidekick. The fork's
   image declares `VOLUME /opt/data` and uses it as `HOME` (caches, `.local`), so the sidekick home is `/opt/data/sidekick` and that junk stays out
   of the story. The fork's entrypoint runs the container command as user `hermes` (uid 10000, remappable with `HERMES_UID`) under s6; its own
   `main-hermes` service is `sleep infinity`, so `CMD ["sksidekick", "serve"]` makes `serve` the only gateway supervisor. No port is published by
   default (the first-run page of Plan 3 adds one).
7. **Plain installer:** `install.sh` uses `uv` to install a pinned Python, Hermes (from the fork tag via its `pm` installer), skmemory and sksidekick
   into `~/.local/share/sksidekick-app/` (code), creates the home, runs `sksidekick init` (the phrase is shown in the terminal), and installs a
   launchd agent (`io.skworld.sksidekick`) or a systemd user unit (`sksidekick.service`). `install.sh --uninstall` removes code and service, never the home.

## Global Constraints

- Hermes pinned at `v0.23.1-sk.4`, hermes-skmemory at `v0.2.0`; every other version pinned in `packaging/versions.env` (one file, Renovate-readable).
- The image and installer never send anything anywhere except to the model tier and channels the user chose, and to backup destinations.
- The recovery phrase appears only on the user's terminal or the first-run page, never in a log, a channel, or the image build output.
- macOS Apple Silicon and Linux x86_64/arm64 only; no Windows paths.
- No em or en dashes in any file.

## Review Focus

1. **Memory written outside the home** (`HERMES_HOME` pointed elsewhere, or the plugin switched to linked mode): backups would silently miss
   memories. `serve` refuses to start, and a test runs a real Hermes turn and asserts the memory file appears in the home's plugin store. (Tasks 2, 3)
2. **Two copies answering after a move** (old container keeps running): the old copy stops within 5 s of `move-out`. (Task 3 test with a real child)
3. **Disk full or the volume read-only:** `serve` reports it to the user and keeps the gateway running rather than crash-looping. A backup that fails
   for lack of space never deletes old backups (Plan 1 already guarantees rotation only after success). (Task 3)
4. **Container restarted mid-backup:** the next start finds no `.part` files counted as backups and a stale status, and backs up again. (Task 3)
5. **macOS sleep:** a laptop asleep past the backup time backs up on wake, and staleness is computed from wall time, not from timers that slept. (Task 5)

---

## Task 1: Story exclusions and credential redaction in the core

**Files:** Modify `src/sksidekick/home.py`, `src/sksidekick/archive.py`, `src/sksidekick/cli.py`; tests `tests/test_home.py`, `tests/test_archive.py`.

**Interfaces:**
- `home.EXCLUDE`: tuple of relative prefixes and globs from Decision 1; `Home.story_paths()` honours it.
- `archive.export(home, dest, *, with_credentials: bool = True)`; `backup.run_backup` always passes `True`; CLI `export` passes `--with-credentials`
  (default False). Redaction: in `hermes/.env` every `KEY=value` becomes `KEY=__REMOVED__`; in `hermes/config.yaml` every mapping value whose key
  matches `(?i)(token|secret|password|api_key|apikey)` becomes `__REMOVED__`. The inner manifest records `"redacted": [paths]`.
- `import_archive` returns the inner manifest; the CLI prints "Log in again for: <channels>" when `redacted` is non-empty.

- [ ] Tests first: `test_hermes_logs_tools_cache_plugins_excluded`, `test_hermes_sessions_included`, `test_export_without_credentials_redacts_env_and_config`, `test_backup_keeps_credentials`, `test_redacted_listed_in_manifest`. Implement, pass, commit `feat(core): story exclusions and credential redaction`.

## Task 2: Image

**Files:** Create `packaging/Dockerfile`, `packaging/versions.env`, `packaging/hermes-config.yaml` (template: `memory.provider: skmemory`, linked mode, `SKMEMORY_HOME` from env), `packaging/entrypoint.sh`, `.github/workflows/image.yml`, `tests/image/test_image.sh`.

**Interfaces:**
- Image env: `SKSIDEKICK_HOME=/opt/data/sidekick`, `HERMES_HOME=/opt/data/sidekick/hermes`; command `sksidekick serve` (Task 3); with no home
  yet it prints "Run: docker exec -it <name> sksidekick init" and waits (no crash loop).
- `image.yml`: on PR builds amd64 and arm64 (no push); on tag `sksidekick-v*` pushes to GHCR with the tag and digest; uses GitHub OIDC, no stored token.

- [ ] `tests/image/test_image.sh` first: build, `docker run` with a temp volume, `sksidekick init` (phrase printed), `hermes --version` shows `0.23.1-sk.4`,
  `skmemory-mcp` on PATH, config has `memory.provider: skmemory`; one scripted Hermes turn against a stub OpenAI-compatible model server (a 20-line
  Python HTTP server in `tests/image/stub_model.py`) that triggers a memory store, then assert a memory file under
  `/opt/data/sidekick/hermes/.skmemory/agents/sidekick/memory/` and that a backup made right after holds it; then
  `sksidekick backup add-dir /opt/data/usb && backup run && drill` exits 0. Implement until it passes on amd64 locally
  and both arches in CI. Commit `feat(image): sksidekick OCI image`.

## Task 3: `sksidekick serve` supervisor

**Files:** Create `src/sksidekick/serve.py`, `tests/test_serve.py`; modify `cli.py` (`serve` subcommand).

**Interfaces:**
- `serve(home: Home, *, gateway_argv: list[str], clock=time.monotonic, wall=datetime.now, sleep=time.sleep, notify: Callable[[str], None]) -> int`.
  Checks: `require_live` (refuse, exit 3, message "this copy is not the live sidekick"), `hermes/.skmemory/` empty (refuse, exit 3). Starts the
  gateway child; restarts it with backoff 5 s doubling to 5 min on exit; every 60 s re-checks `require_live` and stops the child (SIGTERM, 20 s,
  SIGKILL) if not live; runs `run_backup` when `backup.status(...).stale` or the interval passed; runs `run_drill` every 7 days.
- `notify` default appends to `state/notices.jsonl` and, when `config/serve.json` sets `notify_to` (a platform, for example `telegram`), runs
  `hermes send --to <platform> --quiet "<text>"`, which reaches that platform's home channel (Hermes sk.4 has no `owner` target); messages, exactly:
  - "Backup failed: <error>. Your last good backup is from <date>." (or "You have no backup yet.")
  - "Your restore test failed: <error>. Your backups may not restore. Please check your backup destination."
  - "Your sidekick's disk is full. It keeps running, but it cannot save backups until there is space."

- [ ] Tests first with a fake gateway (a `sleep 3600` child) and fake clock: `test_refuses_when_not_live`, `test_refuses_when_plugin_store_has_files`,
  `test_stops_gateway_within_60s_after_move_out`, `test_backup_runs_when_stale_on_start`, `test_backup_failure_notifies_user_with_last_good_date`,
  `test_drill_every_7_days`, `test_gateway_restart_backoff`, `test_disk_full_notifies_and_keeps_running`. Implement, pass, commit `feat(serve): supervisor`.

## Task 4: Plain installer (Linux and macOS)

**Files:** Create `packaging/install.sh`, `packaging/io.skworld.sksidekick.plist`, `packaging/sksidekick.service`, `.github/workflows/installer.yml`.

**Interfaces:** `install.sh [--home PATH] [--uninstall]`. Installs into `~/.local/share/sksidekick-app/` (Linux) or
`~/Library/Application Support/sksidekick-app/` (macOS); home defaults per Architecture; writes `SKSIDEKICK_HOME` into the unit/plist; runs
`sksidekick init` unless the home already holds a sidekick; `--uninstall` stops and removes the service and code, prints the home path and
"Your sidekick's data is still here. To delete it permanently: sksidekick delete --yes".

- [ ] `installer.yml` first (ubuntu-latest and macos-14): run `install.sh --home $RUNNER_TEMP/sk`, assert the service is loaded
  (`launchctl print gui/$UID/io.skworld.sksidekick` or `systemctl --user is-enabled sksidekick`), `sksidekick status` live, then `install.sh --uninstall`
  and assert the home still exists. Implement, pass, commit `feat(installer): plain installer for macOS and Linux`.

## Task 5: Moving between image and installer, and sleep

**Files:** `tests/image/test_move.sh`, `tests/test_serve.py` (sleep case), docs `docs/MOVING.md`.

- [ ] Tests first: `test_move.sh` moves a sidekick from a container to a plain install on the same runner and back (move-out, import with the phrase on
  stdin, retire), asserting the memory written in the first place is present at the end and only the newest copy is live. `test_backup_after_wall_clock_jump`
  (fake wall clock jumps 30 h while the monotonic clock jumps 1 s: backup runs). Write `docs/MOVING.md` (plain steps for a user). Commit
  `test: moving between image and installer`.

## Done when

The image builds for both arches in CI, the installer passes on macOS and Linux runners, a Hermes turn stores memory under the home, a move between
the two keeps everything with one live copy, and the user hears about a failed backup on their own channel. Then Plan 3 (first-run page) starts.
