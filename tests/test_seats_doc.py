"""Run every `sksidekick-seat` command in docs/SEATS.md against temp paths.

Rules: a command is a line in a fenced block that starts with `sksidekick-seat`. Placeholders ($SEATS,
$ESTATE, $REGISTRY, $INVITE, $NEW_INVITE, $USAGE_JSON, $CLIENT_ID) are replaced. $CLIENT_ID is the id printed by the
first `add`. Lines with --reload-unit are skipped: they need a live systemd gateway unit.
"""
import json
import re
import shlex
from pathlib import Path

import pytest

from sksidekick.seats.cli import main

DOC = Path(__file__).resolve().parent.parent / "docs" / "SEATS.md"


def doc_commands():
    cmds, in_block = [], False
    for line in DOC.read_text().splitlines():
        if line.startswith("```"):
            in_block = not in_block
        elif in_block and line.startswith("sksidekick-seat "):
            cmds.append(line)
    return cmds


def test_doc_has_every_command():
    text = " ".join(doc_commands())
    for v in ("init", "add", "list", "rotate", "remove", "usage"):
        assert re.search(rf'--estate "\$ESTATE" {v}\b', text), v


def test_no_dashes_in_doc():
    assert "—" not in DOC.read_text() and "–" not in DOC.read_text()


def test_doc_commands_run(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("SKWORLD_ESTATE", raising=False)
    usage = tmp_path / "usage.json"
    usage.write_text(json.dumps({"today": {"clients": []}, "spend": {"rows": []}}))
    subs = {"$SEATS": str(tmp_path / "seats"), "$ESTATE": "example", "$REGISTRY": str(tmp_path / "clients.json"),
            "$INVITE": str(tmp_path / "invite.json"), "$NEW_INVITE": str(tmp_path / "invite-2.json"), "$USAGE_JSON": str(usage)}
    ran = 0
    for line in doc_commands():
        if "--reload-unit" in line:
            continue
        argv = []
        for tok in shlex.split(line)[1:]:
            argv.append(subs.get(tok, tok))
        capsys.readouterr()
        assert main(argv) == 0, line
        out = capsys.readouterr().out
        m = re.search(r"sk-[0-9a-f]{12}", out)
        if m and "$CLIENT_ID" not in subs:
            subs["$CLIENT_ID"] = m.group(0)
        ran += 1
    assert ran >= 6
