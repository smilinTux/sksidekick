# sksidekick build plan (roadmap) and Plan 1: the story-safe core

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** sksidekick design, approved by Chef 2026-11-20: (private design doc)
(`skhub-nor:GTD/projects/sidekick/sksidekick/sksidekick-design-2026-11-15.md`). Read it with this plan.

## Roadmap (one plan per part, each ships working software)

The design's build order says the image and installer ship only once export, import, backup and delete work
("A sidekick that can lose a story does not ship"). So the parts are:

| Plan | Part | Delivers | Depends on |
|---|---|---|---|
| **1 (this file)** | Story-safe core | `sksidekick` Python package and CLI: keys and recovery phrase, encrypted export and import, plain export, backups with rotation and status, restore drill, reset, permanent delete (crypto-shred), move-out and retire | nothing |
| 2 | Container image and plain installer | One OCI image (Hermes v0.23.1-sk.4 + hermes-skmemory v0.2.0 + skmemory local tier + skcapstone + sksidekick), Linux x86/ARM; macOS (Apple Silicon) plain installer as the primary Mac path | 1 |
| 3 | First-run setup page | Small web page served by the sidekick: tier, channels, the 4 personalities with audio, voice, key and recovery phrase on screen (never in chat), backup destination | 1, 2 |
| 4 | Seats and first users | Seat turn-on through the seats-agent flow, per-sidekick skgateway client id (metered, nobody pays yet), the first three users (one on each supported platform) | 2, 3 |
| 5 | Nextcloud ExApp wrapper | The same image as an AppAPI ExApp; delete also removes the sidekick's Talk conversations on our server | 2, 3 |
| later | Retention, key rotation, account connections, Webtop variant | Rolling retention with per-period keys; key generations; gog and skservices; LinuxServer Webtop option (not v1) | 1 to 5 |

---

# Plan 1: story-safe core

**Goal:** A person's whole sidekick (memory, soul, settings, journal, reminders) can be exported, backed up, test-restored, moved, reset and permanently deleted, with every archive encrypted to a key only the user can recover.

**Architecture:** A small Python package `sksidekick` operating on one sidekick home directory. A home has one key generation: an age X25519 identity kept live in the home (the running sidekick must hold it, the design's agreed hosted caveat) plus a copy wrapped with the recovery phrase. Archives (`.sksk`) are a plain tar envelope holding a public manifest, the phrase-wrapped identity, and the age-encrypted payload, so an archive restores anywhere from the phrase alone. Delete destroys both key copies first, then the files.

**Tech Stack:** Python 3.11+, `pyrage` 1.4.0 (bindings for the audited age format; no home-made crypto), stdlib `argparse`, `tarfile`, `hashlib`, `secrets`; `rclone` (optional, subprocess) for remote backup destinations; pytest.

## Global Constraints

- No home-made crypto: age (X25519 recipients, scrypt passphrase) via `pyrage==1.4.*` only.
- Archive format is documented and open (`docs/ARCHIVE_FORMAT.md`), versioned `sksidekick.archive.v1`.
- The recovery phrase is shown once, on the user's own screen, and never written to disk unwrapped, logged, or sent to a channel.
- Nothing phones home: no network call except to a backup destination the user chose.
- No em or en dashes in any file (CI greps for them). Hyphens are fine.
- Python `>=3.11`; Linux and macOS; no Windows code paths in v1.
- Commit after each task; commit before any long verification.

## Review Focus

1. **Wrong recovery phrase on import:** clean error "recovery phrase does not open this archive", nothing written to the target. (Task 3 test)
2. **Truncated or corrupt archive** (disk full mid-backup, copied half a file): import refuses with "archive damaged", and backups are written to a temp name and renamed only when complete, so a partial file never counts as a backup. (Tasks 3 and 4 tests)
3. **Archive entries that escape the target** (`../`, absolute paths, symlinks): import refuses the whole archive. (Task 3 test)
4. **Import into a home that already holds a story:** refuse; never overwrite. (Task 3 test)
5. **Backup destination unreachable or full:** the run fails loudly, status turns stale, and old backups are not rotated away (rotation happens only after a verified new one). (Task 4 test)

---

## File structure

```
pyproject.toml                    hatchling, name sksidekick, deps pyrage==1.4.*, script sksidekick = sksidekick.cli:main
README.md
docs/ARCHIVE_FORMAT.md            the open format (Task 3)
docs/plans/2026-11-20-plan-1-story-safe-core.md
.github/workflows/ci.yml          pytest on 3.11 and 3.12 (ubuntu, macos); dash grep
src/sksidekick/__init__.py        __version__ = "0.1.0"
src/sksidekick/home.py            Home layout, instance state
src/sksidekick/keys.py            key generation, recovery phrase, shred
src/sksidekick/archive.py         export, plain export, verify, import
src/sksidekick/backup.py          destinations, run, rotation, status
src/sksidekick/drill.py           restore drill
src/sksidekick/lifecycle.py       reset, delete, move-out, retire
src/sksidekick/cli.py             argparse front end
tests/conftest.py                 `home` fixture: an initialized Home in tmp_path with sample memories
tests/test_*.py                   one per module
```

## Task 1: Home layout and instance state

**Files:** Create `src/sksidekick/home.py`, `tests/test_home.py`, `pyproject.toml`, `src/sksidekick/__init__.py`, `tests/conftest.py`, `.github/workflows/ci.yml`, `README.md`.

**Interfaces:**
- Produces: `class Home` with `Home(root: Path)`, properties `memory_dir`, `soul_dir`, `config_dir`, `keys_dir`, `state_dir` (all under root: `memory/`, `soul/`, `config/`, `keys/`, `state/`); `journal_path = root/"journal.md"`.
- `Home.story_paths() -> list[Path]`: relative paths of every file that is part of the story: everything under root except `keys/`, `state/`, and any `index.db*` (rebuildable). Sorted.
- `Home.is_empty() -> bool`: True when `story_paths()` is empty and no `keys/` files exist.
- `Home.instance() -> dict` reading `state/instance.json` (`{"instance_id": str, "status": "live"|"moving"|"retired"}`); `Home.set_status(status: str) -> None`; `Home.new_instance() -> str` writes a fresh `secrets.token_hex(8)` id with status `live`.

- [ ] **Step 1:** Tests: `test_story_paths_skip_keys_state_and_index` (files `memory/long-term/a.json`, `memory/index.db`, `memory/index.db-wal`, `keys/x`, `state/instance.json`, `soul/base.json` → `["memory/long-term/a.json", "soul/base.json"]`); `test_is_empty_on_fresh_dir`; `test_instance_roundtrip` (new_instance then set_status("moving") → status moving, same id); `test_set_status_rejects_unknown` (ValueError).
- [ ] **Step 2:** Run `pytest tests/test_home.py -q`, expect import failure.
- [ ] **Step 3:** Implement `home.py`; scaffold pyproject, CI (pytest plus `! grep -rnP '\x{2014}|\x{2013}' --include='*.py' --include='*.md' .`), README stub, conftest `home` fixture (Home in tmp_path, `memory/{short-term,mid-term,long-term}/` with two JSON memories each, `soul/base.json`, `journal.md`).
- [ ] **Step 4:** Tests pass.
- [ ] **Step 5:** Commit `feat(home): sidekick home layout and instance state`.

## Task 2: Keys and recovery phrase

**Files:** Create `src/sksidekick/keys.py`, `tests/test_keys.py`.

**Interfaces:**
- Consumes: `Home`.
- Produces: `generate_phrase() -> str`: 8 groups of 5 characters from the Crockford base32 alphabet (`0123456789ABCDEFGHJKMNPQRSTVWXYZ`) joined by `-` (200 bits), from `secrets`.
- `normalize_phrase(phrase: str) -> str`: uppercase, strip spaces and hyphens, map `O`→`0`, `I`/`L`→`1`, then regroup; raises `ValueError` on wrong length or characters.
- `create_keys(home: Home) -> str`: raises `FileExistsError` if `keys/` already has files; generates an identity, writes `keys/live.identity` (mode 0600, the age secret key text) and `keys/recovery.identity.age` (identity wrapped with the phrase via `pyrage.passphrase.encrypt`), writes `keys/recipient.txt` (public key), returns the phrase. The phrase is never written anywhere.
- `load_identity(home: Home) -> x25519.Identity`, `recipient(home: Home) -> x25519.Recipient`.
- `unwrap_identity(wrapped: bytes, phrase: str) -> x25519.Identity`: raises `WrongPhrase` (subclass of `ValueError`) on `pyrage.DecryptError`.
- `shred_keys(home: Home) -> list[str]`: for each file in `keys/`: overwrite with random bytes of the same length, fsync, unlink; returns the names destroyed.

- [ ] **Step 1:** Tests: `test_phrase_shape` (regex `^([0-9A-HJKMNP-TV-Z]{5}-){7}[0-9A-HJKMNP-TV-Z]{5}$`); `test_normalize_accepts_lowercase_spaces_and_lookalikes`; `test_create_keys_writes_three_files_and_no_phrase` (phrase string not found in any file under home); `test_live_identity_mode_0600`; `test_create_keys_twice_refuses`; `test_unwrap_with_phrase_matches_recipient`; `test_unwrap_wrong_phrase_raises_WrongPhrase`; `test_shred_removes_all_key_files`.
- [ ] **Step 2:** Run, expect failures.
- [ ] **Step 3:** Implement `keys.py`.
- [ ] **Step 4:** Tests pass.
- [ ] **Step 5:** Commit `feat(keys): key generation, recovery phrase, shred`.

## Task 3: Archive format, export, verify, import, plain export

**Files:** Create `src/sksidekick/archive.py`, `docs/ARCHIVE_FORMAT.md`, `tests/test_archive.py`.

**Interfaces:**
- Consumes: `Home`, `keys.recipient`, `keys.load_identity`, `keys.unwrap_identity`.
- Produces: `FORMAT = "sksidekick.archive.v1"`.
- `export(home: Home, dest: Path) -> Path`: builds the payload (tar.gz of `story_paths()` plus `inner-manifest.json` = `{"format", "created", "instance_id", "files": {relpath: sha256}}`), encrypts it to `recipient(home)`, writes the outer tar to `dest.with_suffix(".sksk.part")` then `os.replace` to `dest`. Outer tar members, in order: `manifest.json` (`{"format", "created", "recipient", "payload_sha256"}`, no file names), `recovery.identity.age` (copied from keys), `payload.age`. Returns `dest`.
- `verify(path: Path, identity: x25519.Identity) -> dict`: returns the inner manifest after checking: outer members exactly the three names, `payload_sha256` matches, payload decrypts, every listed file present with matching sha256, no member path that is absolute, contains `..`, or is a link or device. Raises `ArchiveDamaged(ValueError)` otherwise.
- `open_identity(path: Path, phrase: str) -> x25519.Identity`: reads `recovery.identity.age` and calls `unwrap_identity`.
- `import_archive(path: Path, target: Home, *, phrase: str | None = None, identity: x25519.Identity | None = None) -> dict`: refuses `FileExistsError` unless `target.is_empty()`; gets the identity from `identity` or `open_identity(path, phrase)`; `verify`; extracts the story files into a temp dir inside the target's parent then moves them in; restores keys: writes `keys/live.identity` from the identity, `keys/recovery.identity.age` and `keys/recipient.txt` from the archive; calls `target.new_instance()`. Returns the inner manifest.
- `export_plain(home: Home, dest_dir: Path) -> int`: copies the story files decrypted into `dest_dir` (must not exist) and returns the count. For leaving the ecosystem.

- [ ] **Step 1:** Tests: `test_roundtrip_with_phrase` (export, import into a fresh Home with the phrase: same `story_paths()` and same bytes); `test_roundtrip_with_live_identity`; `test_manifest_has_no_file_names` (outer manifest JSON contains no `memory/` substring); `test_wrong_phrase_writes_nothing` (WrongPhrase, target still empty); `test_truncated_archive_is_damaged` (cut last 100 bytes → ArchiveDamaged); `test_tampered_payload_is_damaged` (flip a byte in payload.age → ArchiveDamaged); `test_member_with_dotdot_is_refused` (hand-built payload containing `../evil` → ArchiveDamaged, no file outside target); `test_symlink_member_is_refused`; `test_import_into_nonempty_home_refuses`; `test_no_part_file_left_after_export`; `test_plain_export_matches_story`.
- [ ] **Step 2:** Run, expect failures.
- [ ] **Step 3:** Implement `archive.py`. Extraction checks each member before writing (never `extractall` on untrusted input). Write `docs/ARCHIVE_FORMAT.md`: the three members, both manifests' fields, the age modes used, how to open an archive with the stock `age` CLI and the phrase (decrypt `recovery.identity.age` with `age -d`, then `age -d -i` the payload, then `tar xzf`).
- [ ] **Step 4:** Tests pass. Also run the `age` CLI recipe from the doc by hand if `age` is installed; note the result in the commit.
- [ ] **Step 5:** Commit `feat(archive): open encrypted archive format, export, verify, import, plain export`.

## Task 4: Backups

**Files:** Create `src/sksidekick/backup.py`, `tests/test_backup.py`.

**Interfaces:**
- Consumes: `archive.export`, `archive.verify`, `keys.load_identity`.
- Produces: config file `config/backup.json`: `{"destinations": [{"kind": "dir", "path": str} | {"kind": "rclone", "remote": str}], "keep": 7, "interval_hours": 24}`; `load_config(home) -> dict` (defaults when missing: no destinations, keep 7, interval 24).
- `run_backup(home: Home, *, now: datetime | None = None) -> list[dict]`: exports once to a temp file, verifies it with the live identity, then for each destination copies it in as `sidekick-<instance_id>-<UTC yyyymmddTHHMMSSZ>.sksk` (dir: copy to `.part` then `os.replace`; rclone: `rclone copyto` to the final name then `rclone lsf` to confirm); rotation deletes the oldest beyond `keep` only for destinations whose copy succeeded. Writes `state/backup.json` `{"last_success": iso, "last_error": str|None, "results": [...]}`. Returns per-destination `{"destination", "ok", "name"|"error"}`. Raises nothing for a destination failure; records it.
- `status(home: Home, *, now: datetime | None = None) -> dict`: `{"last_success", "stale": bool, "last_error"}`; stale when there was never a success or `now - last_success > 2 * interval_hours`.
- `latest(home: Home) -> Path | None`: newest backup in the first `dir` destination.

- [ ] **Step 1:** Tests: `test_backup_to_dir_writes_verified_archive`; `test_rotation_keeps_n` (keep 3, 5 runs with advancing `now` → 3 files, newest kept); `test_unreachable_destination_records_error_and_keeps_old` (dir destination made read-only after 2 good runs: result ok False, still 2 files, status `last_error` set); `test_status_stale_after_two_intervals`; `test_status_stale_when_never_run`; `test_no_destinations_is_an_error_not_silent` (result says no destination configured, status stale); `test_rclone_destination_uses_copyto` (monkeypatch `subprocess.run`, assert argv).
- [ ] **Step 2:** Run, expect failures.
- [ ] **Step 3:** Implement `backup.py`.
- [ ] **Step 4:** Tests pass.
- [ ] **Step 5:** Commit `feat(backup): verified backups with rotation and staleness`.

## Task 5: Restore drill

**Files:** Create `src/sksidekick/drill.py`, `tests/test_drill.py`.

**Interfaces:**
- Consumes: `backup.latest`, `archive.import_archive`, `keys.load_identity`.
- Produces: `run_drill(home: Home) -> dict`: imports `latest(home)` into a fresh `tempfile.TemporaryDirectory()` Home using the live identity, compares the restored `story_paths()` count with the inner manifest, removes the temp dir, writes `state/drill.json` `{"at", "ok", "backup", "files", "error"}` and returns it. No backup → ok False, error "no backup to test".

- [ ] **Step 1:** Tests: `test_drill_passes_on_good_backup`; `test_drill_fails_on_damaged_backup` (truncate latest → ok False, error mentions damaged); `test_drill_without_backup`; `test_drill_leaves_no_temp_dir` (monkeypatch tempfile dir, assert empty after).
- [ ] **Step 2 to 4:** Run failing, implement, pass.
- [ ] **Step 5:** Commit `feat(drill): restore drill on the latest backup`.

## Task 6: Reset, delete, move-out, retire

**Files:** Create `src/sksidekick/lifecycle.py`, `tests/test_lifecycle.py`.

**Interfaces:**
- Consumes: `Home`, `keys.shred_keys`, `archive.export`.
- Produces: `plan_reset(home, *, keep_settings: bool) -> list[str]` (relpaths that reset would remove: all of `memory/` and `journal.md`; plus `soul/` and `config/` unless keep_settings). `reset(home, *, keep_settings: bool, export_to: Path | None) -> dict` (exports first when `export_to` is given, then removes the planned paths; keys and instance stay; returns `{"removed": n, "export": path|None}`).
- `delete(home) -> dict`: order matters: `shred_keys` first, then remove every remaining file and the root. Returns `{"keys_destroyed": [...], "files_removed": n, "notice": NOTICE}` where `NOTICE` is the exact text: "Your sidekick's keys are destroyed, so its memory and every backup made with them can no longer be opened by anyone holding only those files. Destroy any printed recovery phrase and any plain exports you made: the sidekick cannot reach those. Outside channels such as Telegram or Slack keep their own copy of your conversations."
- `move_out(home, dest: Path) -> Path`: export to dest, `set_status("moving")`, returns the archive path. `retire(home) -> dict`: requires status `moving` (else `RuntimeError`), then `delete(home)`.
- `require_live(home) -> None`: raises `RuntimeError("this copy is not the live sidekick")` unless status is `live`. (Plan 2 calls it before serving any channel; that is how exactly one copy answers.)

- [ ] **Step 1:** Tests: `test_plan_reset_keep_settings`; `test_reset_exports_first_then_wipes_memory` (export exists and imports back to the pre-reset story); `test_delete_shreds_keys_before_files` (monkeypatch file removal to raise after keys: keys already gone); `test_delete_notice_text_exact`; `test_backup_unreadable_after_delete` (backup made, delete, then `verify` with a fresh unrelated identity fails and the old live identity file no longer exists); `test_move_out_sets_moving_and_require_live_refuses`; `test_retire_requires_moving`.
- [ ] **Step 2 to 4:** Run failing, implement, pass.
- [ ] **Step 5:** Commit `feat(lifecycle): reset, permanent delete, move-out and retire`.

## Task 7: CLI

**Files:** Create `src/sksidekick/cli.py`, `tests/test_cli.py`; update `README.md` with the command list.

**Interfaces:**
- Consumes: everything above.
- Produces: `main(argv: list[str] | None = None) -> int`. Global `--home PATH` (default `$SKSIDEKICK_HOME`, else error "no sidekick home: pass --home or set SKSIDEKICK_HOME"; no implicit default, same rule as skbrain). Subcommands and exit codes:
  - `init`: create layout, keys, instance; print the phrase once between two blank lines with "Write this down. It is the only way to restore your sidekick." Exit 0.
  - `export PATH`, `export --plain DIR`, `verify PATH [--phrase-stdin]`, `import PATH --phrase-stdin` (phrase read from stdin, never argv).
  - `backup run`, `backup status` (exit 1 when stale), `backup add-dir PATH`, `backup add-rclone REMOTE`.
  - `drill` (exit 1 on failure).
  - `reset [--keep-settings] [--export-to PATH] --yes` (without `--yes`: print the plan, exit 2).
  - `delete --yes` (without: print what would be destroyed, exit 2), `move-out PATH`, `retire --yes`, `status` (instance, backup status, last drill as JSON).

- [ ] **Step 1:** Tests: `test_no_home_is_an_error`; `test_init_prints_phrase_once_and_never_stores_it`; `test_import_reads_phrase_from_stdin_only` (argparse has no `--phrase` option); `test_backup_status_exit_1_when_stale`; `test_delete_without_yes_exits_2_and_changes_nothing`; `test_full_story` (init, add memory, backup add-dir, backup run, drill ok, move-out, import elsewhere with phrase, retire old: old home gone, new home live with the memory).
- [ ] **Step 2 to 4:** Run failing, implement, pass.
- [ ] **Step 5:** Commit `feat(cli): sksidekick command line`. Then full suite, dash grep, push, PR.

## Done when

All tests green on CI (Linux and macOS), `docs/ARCHIVE_FORMAT.md` lets someone open an archive with stock `age` and their phrase, and the full-story CLI test passes. Then Plan 2 starts.
