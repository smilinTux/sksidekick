# sksidekick

Your own AI sidekick: one person, one personality, one memory that follows you to every channel, and stays
yours to export, move or permanently delete. Design approved 2026-11-20; build plan in `docs/plans/`.

This repo currently holds **Plan 1, the story-safe core**: the part that must work before anything else ships.

## First run

The first time, a setup page in your browser walks you through five steps: where the model runs, which
channels to use (none is fine), the personality and voice, your recovery phrase, and where backups go.

- **Plain install** (macOS, Linux): `bash packaging/install.sh` installs, then opens the setup page.
  `--setup-headless` prints its address instead of opening a browser; `--init` skips the page and shows
  the phrase in the terminal (`sksidekick init`). If setup stops (Ctrl-C, a closed terminal), run
  `sksidekick-setup` to go on where it stopped (`--headless` to only print the address).
- **Container**: publish the setup port on your own computer only, then open the address from the log:

  ```sh
  docker run -d --name sidekick -p 127.0.0.1:8765:8765 -v sidekick:/opt/data IMAGE
  docker logs sidekick    # Open http://127.0.0.1:8765/?t=...
  ```

The recovery phrase is shown once, on your screen, and nowhere else: it is never saved, logged or sent.
Write it down. The page listens only on this computer and loads nothing from the internet; nothing leaves
your machine during setup except one check that the model address you enter answers. Setup runs once:
when it is done the page closes and your sidekick starts. You can still use `sksidekick init` or
`sksidekick import` instead; setup then steps aside.

## Commands

Every command needs `--home PATH` or `SKSIDEKICK_HOME` (there is no default home, on purpose).

| Command | What it does |
|---|---|
| `sksidekick setup [--host H] [--port N]` | The first-run setup page (127.0.0.1:8765 by default). |
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
