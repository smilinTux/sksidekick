"""tests/leak_search.sh, the phrase leak check the installer and image tests share (GNU and BSD alike)."""

from __future__ import annotations

import os
import socket
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parent / "leak_search.sh"
PHRASE = "ABCDE-FGHJK-MNPQR-STVWX-YZ012-34567-89ABC-DEFGH"


def search(*paths: Path, patterns: str = PHRASE + "\n") -> subprocess.CompletedProcess:
    return subprocess.run(["sh", str(SCRIPT), *map(str, paths)], input=patterns, capture_output=True, text=True,
                          timeout=30)


def test_clean_tree_with_a_socket_and_an_unreadable_file(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "notes.txt").write_text("nothing here\n")
    sock = socket.socket(socket.AF_UNIX)
    sock.bind(str(tmp_path / "a" / "gateway.sock"))  # BSD grep -r exits 2 on this even with -s
    locked = tmp_path / "a" / "locked"
    locked.write_text(PHRASE)
    locked.chmod(0)
    try:
        r = search(tmp_path)
        assert r.returncode == 0 and r.stdout == "", r.stderr
        assert PHRASE not in r.stdout + r.stderr
    finally:
        sock.close()
        locked.chmod(0o600)


def test_a_leak_is_named(tmp_path):
    (tmp_path / "deep" / "er").mkdir(parents=True)
    (tmp_path / "deep" / "er" / "leak.log").write_text(f"x {PHRASE.replace('-', '')} y\n")
    (tmp_path / "with space.txt").write_text(PHRASE)
    r = search(tmp_path, patterns=f"{PHRASE}\n{PHRASE.replace('-', '')}\n")
    assert r.returncode == 0
    assert sorted(r.stdout.splitlines()) == sorted([str(tmp_path / "deep" / "er" / "leak.log"), str(tmp_path / "with space.txt")])
    assert PHRASE not in r.stdout + r.stderr


def test_search_failures_are_exit_2(tmp_path):
    assert search(tmp_path / "missing").returncode == 2
    assert search(tmp_path, patterns="").returncode == 2
    assert search().returncode == 2
    if os.geteuid() != 0:
        shut = tmp_path / "shut"
        shut.mkdir()
        shut.chmod(0)
        try:
            r = search(tmp_path)
            assert r.returncode == 2 and "could not list" in r.stderr
        finally:
            shut.chmod(0o700)


def test_an_empty_tree_never_reads_stdin(tmp_path):
    r = search(tmp_path)
    assert r.returncode == 0 and r.stdout == ""
