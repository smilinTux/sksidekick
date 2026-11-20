from __future__ import annotations

import io
import json
import re
from pathlib import Path

import pytest

from sksidekick import cli
from sksidekick.home import Home

PHRASE = re.compile(r"([0-9A-HJKMNP-TV-Z]{5}-){7}[0-9A-HJKMNP-TV-Z]{5}")


def run(capsys, *argv, stdin: str = "", monkeypatch=None) -> tuple[int, str]:
    if monkeypatch is not None:
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out + out.err


def _init(capsys, root: Path) -> str:
    code, out = run(capsys, "--home", str(root), "init")
    assert code == 0
    return PHRASE.search(out).group(0)


def test_no_home_is_an_error(capsys, monkeypatch):
    monkeypatch.delenv("SKSIDEKICK_HOME", raising=False)
    code, out = run(capsys, "status")
    assert code == 2 and cli.NO_HOME in out


def test_init_prints_phrase_once_and_never_stores_it(capsys, tmp_path: Path):
    root = tmp_path / "sk"
    code, out = run(capsys, "--home", str(root), "init")
    assert code == 0 and len(PHRASE.findall(out)) == 1
    phrase = PHRASE.search(out).group(0)
    for f in root.rglob("*"):
        if f.is_file():
            assert phrase.encode() not in f.read_bytes()
    code, out = run(capsys, "--home", str(root), "init")
    assert code == 1 and "already holds" in out


def test_import_reads_phrase_from_stdin_only():
    opts = {o for a in cli._parser()._subparsers._group_actions[0].choices["import"]._actions for o in a.option_strings}
    assert "--phrase" not in opts and "--phrase-stdin" in opts


def test_backup_status_exit_1_when_stale(capsys, tmp_path: Path):
    root = tmp_path / "sk"
    _init(capsys, root)
    code, out = run(capsys, "--home", str(root), "backup", "status")
    assert code == 1 and json.loads(out)["stale"] is True


def test_delete_without_yes_exits_2_and_changes_nothing(capsys, tmp_path: Path):
    root = tmp_path / "sk"
    _init(capsys, root)
    before = sorted(str(p) for p in root.rglob("*"))
    code, _ = run(capsys, "--home", str(root), "delete")
    assert code == 2 and sorted(str(p) for p in root.rglob("*")) == before


def test_wrong_phrase_is_a_clean_error(capsys, tmp_path: Path, monkeypatch):
    root = tmp_path / "sk"
    _init(capsys, root)
    run(capsys, "--home", str(root), "export", str(tmp_path / "a.sksk"))
    code, out = run(
        capsys, "--home", str(tmp_path / "other"), "import", str(tmp_path / "a.sksk"), "--phrase-stdin",
        stdin="AAAAA-AAAAA-AAAAA-AAAAA-AAAAA-AAAAA-AAAAA-AAAAA\n", monkeypatch=monkeypatch,
    )
    assert code == 1 and "recovery phrase does not open this archive" in out
    assert Home(tmp_path / "other").is_empty()


def test_full_story(capsys, tmp_path: Path, monkeypatch):
    old = tmp_path / "laptop"
    phrase = _init(capsys, old)
    mem = old / "memory" / "long-term" / "first.json"
    mem.parent.mkdir(parents=True, exist_ok=True)
    mem.write_text('{"text": "we met today"}')
    assert run(capsys, "--home", str(old), "backup", "add-dir", str(tmp_path / "usb"))[0] == 0
    assert run(capsys, "--home", str(old), "backup", "run")[0] == 0
    code, out = run(capsys, "--home", str(old), "drill")
    assert code == 0 and json.loads(out)["ok"] is True
    assert run(capsys, "--home", str(old), "move-out", str(tmp_path / "move.sksk"))[0] == 0
    new = tmp_path / "pi"
    code, out = run(
        capsys, "--home", str(new), "import", str(tmp_path / "move.sksk"), "--phrase-stdin",
        stdin=phrase.lower() + "\n", monkeypatch=monkeypatch,
    )
    assert code == 0, out
    assert run(capsys, "--home", str(old), "retire", "--yes")[0] == 0
    assert not old.exists()
    assert (new / "memory" / "long-term" / "first.json").read_text() == '{"text": "we met today"}'
    code, out = run(capsys, "--home", str(new), "status")
    assert json.loads(out)["instance"]["status"] == "live"


@pytest.mark.parametrize("cmd", [["reset"], ["retire"]])
def test_destructive_commands_need_yes(capsys, tmp_path: Path, cmd):
    root = tmp_path / "sk"
    _init(capsys, root)
    assert run(capsys, "--home", str(root), *cmd)[0] == 2
