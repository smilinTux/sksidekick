# sksidekick

Your own AI sidekick: one person, one personality, one memory that follows you to every channel, and stays
yours to export, move or permanently delete. Design approved 2026-11-20; build plan in `docs/plans/`.

This repo currently holds **Plan 1, the story-safe core**: the part that must work before anything else ships.

## Commands

Every command needs `--home PATH` or `SKSIDEKICK_HOME` (there is no default home, on purpose).

| Command | What it does |
|---|---|
| `sksidekick init` | Create a sidekick home and its key, and show the recovery phrase once. Write it down. |
| `sksidekick export FILE.sksk` | Encrypted archive of the whole story. `--plain DIR` writes a readable copy instead. |
| `sksidekick verify FILE.sksk [--phrase-stdin]` | Check an archive completely. |
| `sksidekick import FILE.sksk --phrase-stdin` | Restore into an empty home; the phrase is read from stdin, never from the command line. |
| `sksidekick backup add-dir PATH` / `add-rclone REMOTE` | Add a backup destination (a USB drive, your Nextcloud, any rclone remote). |
| `sksidekick backup run` / `status` | Make a verified backup to every destination; status exits 1 when backups are stale. |
| `sksidekick drill` | Test-restore the latest backup in a scratch space. |
| `sksidekick reset [--keep-settings] [--export-to F] --yes` | Wipe memory and start fresh. |
| `sksidekick move-out FILE.sksk` then `retire --yes` | Move to a new place: this copy stops answering at once, and is destroyed after the new place works. |
| `sksidekick delete --yes` | Permanent delete: destroys the keys first, then every file. |
| `sksidekick status` | Instance, backup and drill state as JSON. |

Archives use an open format built from tar and [age](https://age-encryption.org): see
[docs/ARCHIVE_FORMAT.md](docs/ARCHIVE_FORMAT.md), including how to open one with stock tools.

## Development

```sh
python -m venv .venv && .venv/bin/pip install -e '.[dev]' && .venv/bin/pytest -q
```
