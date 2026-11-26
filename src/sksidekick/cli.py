"""``sksidekick``: command line for the story-safe core."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from sksidekick import __version__, archive, backup, drill, keys, lifecycle
from sksidekick.home import Home

NO_HOME = "no sidekick home: pass --home or set SKSIDEKICK_HOME"


def _phrase_from_stdin() -> str:
    line = sys.stdin.readline()
    if not line.strip():
        raise SystemExit("no recovery phrase on stdin")
    return line.strip()


def _print(obj) -> None:
    print(json.dumps(obj, indent=1, default=str))


def _cmd_init(home: Home, a) -> int:
    if not home.is_empty():
        print(f"{home.root} already holds a sidekick", file=sys.stderr)
        return 1
    for d in (home.memory_dir, home.soul_dir, home.config_dir):
        d.mkdir(parents=True, exist_ok=True)
    os.chmod(home.root, 0o700)
    phrase = keys.create_keys(home)
    try:
        print("Your recovery phrase:\n")
        print(f"    {phrase}\n")
        print("Write this down. It is the only way to restore your sidekick.", flush=True)
    except BaseException:
        keys.shred_keys(home)  # a phrase nobody saw would make every archive unrecoverable
        raise
    home.new_instance()
    return 0


def _cmd_export(home: Home, a) -> int:
    creds = a.with_credentials
    if a.plain:
        n = archive.export_plain(home, Path(a.path), with_credentials=creds)
        print(f"{n} files written, readable by anyone who gets this folder")
    else:
        print(archive.export(home, Path(a.path), with_credentials=creds))
    if not creds:
        print("Channel logins were left out (add --with-credentials to keep them).")
    return 0


def _cmd_verify(home: Home, a) -> int:
    ident = archive.open_identity(Path(a.path), _phrase_from_stdin()) if a.phrase_stdin else keys.load_identity(home)
    m = archive.verify(Path(a.path), ident)
    print(f"ok: {len(m['files'])} files, made {m['created']}")
    return 0


def _cmd_import(home: Home, a) -> int:
    m = archive.import_archive(Path(a.path), home, phrase=_phrase_from_stdin())
    print(f"restored {len(m['files'])} files into {home.root} (archive made {m.get('created')})")
    if m.get("redacted_keys"):
        print(f"Log in again for: {', '.join(m['redacted_keys'])}")
    if m.get("purpose") != "move":
        print("This was a backup or export, not a move. If the original sidekick is still running, stop it: only one copy may answer.")
    return 0


def _cmd_backup(home: Home, a) -> int:
    if a.action == "run":
        res = backup.run_backup(home)
        _print(res)
        return 0 if all(r["ok"] for r in res) else 1
    if a.action == "status":
        st = backup.status(home)
        _print(st)
        return 1 if st["stale"] else 0
    cfg = backup.load_config(home)
    if not a.target:
        print("give a path or rclone remote", file=sys.stderr)
        return 2
    dest = {"kind": "dir", "path": str(Path(a.target).expanduser())} if a.action == "add-dir" else {"kind": "rclone", "remote": a.target}
    cfg["destinations"].append(dest)
    backup.save_config(home, cfg)
    print(f"added {dest}")
    return 0


def _cmd_drill(home: Home, a) -> int:
    res = drill.run_drill(home)
    _print(res)
    return 0 if res["ok"] else 1


def _cmd_reset(home: Home, a) -> int:
    if not a.yes:
        planned = lifecycle.plan_reset(home, keep_settings=a.keep_settings)
        print(f"reset would remove {len(planned)} files. Export first with --export-to PATH. Re-run with --yes.")
        return 2
    _print(lifecycle.reset(home, keep_settings=a.keep_settings, export_to=a.export_to))
    return 0


def _cmd_delete(home: Home, a) -> int:
    if not a.yes:
        print(f"delete would destroy the keys and all {len(home.story_paths())} story files in {home.root}, permanently. "
              "Re-run with --yes.")
        return 2
    res = lifecycle.delete(home)
    print(f"destroyed keys: {', '.join(res['keys_destroyed'])}; removed {res['files_removed']} files.")
    print(res["notice"])
    return 0


def _cmd_move_out(home: Home, a) -> int:
    print(f"packed for the move: {lifecycle.move_out(home, Path(a.path))}")
    print("This copy no longer answers. Import the archive at the new place, check it, then run: sksidekick retire --yes")
    return 0


def _cmd_retire(home: Home, a) -> int:
    if not a.yes:
        print("retire destroys this old copy. Re-run with --yes once the new place works.")
        return 2
    res = lifecycle.retire(home, move_archive=Path(a.archive) if a.archive else None)
    print(f"old copy destroyed ({res['files_removed']} files).")
    return 0


def _cmd_move_cancel(home: Home, a) -> int:
    lifecycle.cancel_move(home)
    print("This copy answers again. If you already imported the move archive somewhere, delete that copy: only one may answer.")
    return 0


def _cmd_serve(home: Home, a) -> int:
    from sksidekick import serve

    return serve.main_serve(home, a.gateway or None)


def _cmd_setup(home: Home, a) -> int:
    if a.start_over:
        from sksidekick.setup import start_over

        return start_over.start_over(home, yes=a.yes)
    if a.yes:
        print("--yes goes with --start-over", file=sys.stderr)
        return 2
    from sksidekick.setup import server

    if a.invite:
        _install_invite(home, a.invite, keep=a.keep_invite)
    elif a.keep_invite:
        print("--keep-invite goes with --invite", file=sys.stderr)
        return 2
    return server.main_setup(home, a.host, a.port)


def _install_invite(home: Home, path: str, *, keep: bool) -> None:
    """Take the operator's invite file. A missing or bad one is one plain line, never its contents, and
    setup still starts with the shared tier disabled."""
    from sksidekick.seats.record import Invite
    from sksidekick.setup import hermes_config, seat

    try:
        invite = Invite.from_json(Path(path).read_text())
        seat.install(home, invite)
    except OSError:
        print(f"could not read the invite file {path}; setup starts without a seat", file=sys.stderr)
        return
    except (ValueError, hermes_config.HermesError) as e:
        print(f"the invite file {path} was not used ({e}); setup starts without a seat", file=sys.stderr)
        return
    if not keep:
        try:
            Path(path).unlink()
        except OSError:
            print(f"the invite was installed but {path} could not be removed; delete it", file=sys.stderr)


def _cmd_status(home: Home, a) -> int:
    _print({"home": str(home.root), "instance": home.instance(), "backup": backup.status(home), "drill": drill.last_drill(home)})
    return 0


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sksidekick", description="Keep your sidekick's story safe.")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--home", help="sidekick home (default: $SKSIDEKICK_HOME)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="create a sidekick home, its key and the recovery phrase").set_defaults(fn=_cmd_init)
    e = sub.add_parser("export", help="encrypted archive of the whole story, or --plain readable copy")
    e.add_argument("path")
    e.add_argument("--plain", action="store_true", help="write a decrypted folder instead")
    e.add_argument("--with-credentials", action="store_true", help="keep channel logins and API keys in the export")
    e.set_defaults(fn=_cmd_export)
    v = sub.add_parser("verify", help="check an archive completely")
    v.add_argument("path")
    v.add_argument("--phrase-stdin", action="store_true", help="open it with the recovery phrase from stdin")
    v.set_defaults(fn=_cmd_verify)
    i = sub.add_parser("import", help="restore an archive into an empty home (phrase on stdin)")
    i.add_argument("path")
    i.add_argument("--phrase-stdin", action="store_true", required=True)
    i.set_defaults(fn=_cmd_import)
    b = sub.add_parser("backup", help="backups: run, status, add-dir, add-rclone")
    b.add_argument("action", choices=["run", "status", "add-dir", "add-rclone"])
    b.add_argument("target", nargs="?")
    b.set_defaults(fn=_cmd_backup)
    sub.add_parser("drill", help="test-restore the latest backup").set_defaults(fn=_cmd_drill)
    r = sub.add_parser("reset", help="wipe memory and start fresh")
    r.add_argument("--keep-settings", action="store_true")
    r.add_argument("--export-to", type=Path)
    r.add_argument("--yes", action="store_true")
    r.set_defaults(fn=_cmd_reset)
    d = sub.add_parser("delete", help="permanently delete (destroys the keys)")
    d.add_argument("--yes", action="store_true")
    d.set_defaults(fn=_cmd_delete)
    m = sub.add_parser("move-out", help="pack for a move; this copy stops answering")
    m.add_argument("path")
    m.set_defaults(fn=_cmd_move_out)
    t = sub.add_parser("retire", help="destroy the old copy after a move")
    t.add_argument("--yes", action="store_true")
    t.add_argument("--archive", help="where the move archive is now, if you moved the file")
    t.set_defaults(fn=_cmd_retire)
    sub.add_parser("move-cancel", help="undo move-out; this copy answers again").set_defaults(fn=_cmd_move_cancel)
    sub.add_parser("status", help="instance, backup and drill state").set_defaults(fn=_cmd_status)
    s = sub.add_parser("serve", help="run the gateway while this copy is live; backups and drills on schedule")
    s.add_argument("gateway", nargs=argparse.REMAINDER, help="gateway command (default: hermes gateway run)")
    s.set_defaults(fn=_cmd_serve)
    u = sub.add_parser("setup", help="first-run setup page in your browser (loopback only by default)")
    u.add_argument("--host", default="127.0.0.1")
    u.add_argument("--port", type=int, default=8765)
    u.add_argument("--start-over", action="store_true",
                   help="throw away an unfinished setup (keys, settings, progress) so it can begin again; never a live sidekick")
    u.add_argument("--yes", action="store_true", help="with --start-over: do not ask first")
    u.add_argument("--invite", metavar="FILE", help="an invite file from whoever runs your gateway; enables the shared option")
    u.add_argument("--keep-invite", action="store_true", help="with --invite: keep the file (it is a secret; it is deleted by default)")
    u.set_defaults(fn=_cmd_setup)
    return p


def main(argv: list[str] | None = None) -> int:
    a = _parser().parse_args(argv)
    root = a.home or os.environ.get("SKSIDEKICK_HOME")
    if not root:
        print(NO_HOME, file=sys.stderr)
        return 2
    try:
        return a.fn(Home(root), a)
    except (keys.WrongPhrase, archive.ArchiveDamaged, RuntimeError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
