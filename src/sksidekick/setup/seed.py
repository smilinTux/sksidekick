"""Give a sidekick its own Hermes home, seeded the way Hermes' installer or boot hook seeded the default one.

One implementation for both paths:
- setup calls ``seed_files`` (through ``state.ensure_hermes_dir``) before its first write into ``hermes/``,
  so its ``hermes config set`` calls and SOUL.md layer on the seed, exactly as after ``sksidekick init``;
- sidekick-start runs ``python -m sksidekick.setup.seed HOME`` after a sidekick exists (setup, init or
  import), which also adds skmemory.json and the skmemory memory provider.

Seeding never runs before a step is taken: a seeded file would make the home non-empty, and then
``sksidekick init`` and ``import`` (still offered beside setup) would refuse it.

Sources come from the environment the container and the plain service set: ``SIDEKICK_SEED`` (a dir with
config.yaml, .env, SOUL.md) and ``SIDEKICK_SKMEMORY_JSON``. Unset means nothing to copy. A file already in
the sidekick's Hermes home is never replaced.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from sksidekick.home import Home

SEED_ENV = "SIDEKICK_SEED"
SKMEMORY_JSON_ENV = "SIDEKICK_SKMEMORY_JSON"
SEED_FILES = ("config.yaml", ".env", "SOUL.md")


def hermes_dir(home: Home) -> Path:
    d = home.hermes_dir
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    return d


def _copy_absent(src: Path, dst: Path) -> None:
    if not dst.exists() and not dst.is_symlink() and src.is_file():
        shutil.copy2(src, dst)  # as `cp -p`


def seed_files(home: Home) -> Path:
    """Create the private hermes/ dir and copy in the seed files it lacks. Returns the dir."""
    d = hermes_dir(home)
    seed = os.environ.get(SEED_ENV)
    if seed:
        for name in SEED_FILES:
            _copy_absent(Path(seed) / name, d / name)
    return d


def seed_all(home: Home) -> Path:
    """seed_files, plus skmemory.json and memory.provider skmemory (what serve needs)."""
    d = seed_files(home)
    sk = os.environ.get(SKMEMORY_JSON_ENV)
    if sk:
        _copy_absent(Path(sk), d / "skmemory.json")
    env = {**os.environ, "HERMES_HOME": str(d)}
    got = subprocess.run(["hermes", "config", "get", "memory.provider"], env=env, capture_output=True, text=True)
    if got.stdout.strip() != "skmemory":
        subprocess.run(["hermes", "config", "set", "memory.provider", "skmemory"], env=env, check=True,
                       stdout=subprocess.DEVNULL)
    return d


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python -m sksidekick.setup.seed HOME", file=sys.stderr)
        return 2
    seed_all(Home(argv[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
