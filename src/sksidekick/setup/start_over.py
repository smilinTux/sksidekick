"""``sksidekick setup --start-over``: throw away an unfinished setup, so setup, init or import can begin again.

Only for a home that is not a sidekick yet (no ``state/instance.json``); a live one is never touched. It
shreds the keys (``keys.shred_keys``: an unconfirmed phrase's keys, or a dead setup's), then removes
``state/setup.json``, ``hermes/`` and ``soul/personality.json``. It never starts the setup server, and it
refuses while one runs on this home: every setup server holds a shared lock on the home directory for as
long as it runs, and start-over needs the lock to itself.
"""

from __future__ import annotations

import fcntl
import os
import shutil
import sys
from collections.abc import Callable

from sksidekick import keys
from sksidekick.home import Home

LIVE = "{root} is a sidekick already, so there is no setup to start over. To begin again from nothing: sksidekick reset or delete."
RUNNING = "setup is running for {root}; stop it first (Ctrl-C in its terminal, or stop the container), then start over."


def hold(home: Home, exclusive: bool) -> int | None:
    """Lock the home directory (created, private, when missing). Returns the fd that holds the lock, or None
    when another process holds it the other way. Closing the fd (or the process ending) lets go."""
    home.root.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(home.root, os.O_RDONLY)
    try:
        fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def _what(home: Home) -> list[str]:
    out = []
    if home.keys_dir.is_dir() and any(home.keys_dir.iterdir()):
        out.append("the keys (and any recovery phrase shown for them)")
    if (home.state_dir / "setup.json").exists():
        out.append("the setup progress")
    if home.hermes_dir.exists():
        out.append("the model, channel and voice settings (hermes/)")
    if (home.soul_dir / "personality.json").exists():
        out.append("the personality choice")
    return out


def start_over(home: Home, yes: bool = False, ask: Callable[[str], str] = input) -> int:
    if (home.state_dir / "instance.json").exists():
        print(LIVE.format(root=home.root), file=sys.stderr)
        return 1
    if not home.root.exists():
        print(f"Nothing to start over in {home.root}: it is empty.")
        return 0
    fd = hold(home, exclusive=True)
    if fd is None:
        print(RUNNING.format(root=home.root), file=sys.stderr)
        return 1
    try:
        if (home.state_dir / "instance.json").exists():  # init or import made it live meanwhile
            print(LIVE.format(root=home.root), file=sys.stderr)
            return 1
        what = _what(home)
        if not what:
            print(f"Nothing to start over in {home.root}.")
            return 0
        if not yes:
            print(f"Start over in {home.root}? This destroys: {'; '.join(what)}.")
            try:
                answer = ask("Type yes to go on: ")
            except EOFError:
                answer = ""
            if answer.strip().lower() != "yes":
                print("Nothing was changed.")
                return 2
        keys.shred_keys(home)
        (home.state_dir / "setup.json").unlink(missing_ok=True)
        for tmp in home.state_dir.glob("*.tmp") if home.state_dir.is_dir() else []:
            tmp.unlink(missing_ok=True)
        if home.hermes_dir.is_symlink():
            home.hermes_dir.unlink()
        elif home.hermes_dir.exists():
            shutil.rmtree(home.hermes_dir)
        (home.soul_dir / "personality.json").unlink(missing_ok=True)
    finally:
        os.close(fd)
    if home.is_empty():
        print(f"Started over: {home.root} is empty again. Run setup again, or sksidekick init or import.")
    else:
        print(f"Setup's files are gone, but {home.root} still holds other files, so init and import will refuse it "
              "until they are moved away. Setup itself can begin again.")
    return 0
