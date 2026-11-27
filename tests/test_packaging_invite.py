"""packaging/sidekick-invite.sh: the root step that makes a mounted 0600 invite readable by setup (issue #8).

The image runs it as root before the drop to hermes. Here it runs as the test user, with the knobs it has
for that: SIDEKICK_AS_HERMES says whether "hermes" can read the file, SIDEKICK_INVITE_OWNER is the test
user, SIDEKICK_DATA and SIDEKICK_ENVDIR are temporary directories.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "packaging" / "sidekick-invite.sh"
CONT_INIT = Path(__file__).resolve().parents[1] / "packaging" / "cont-init.d" / "03-sksidekick-invite"
DOCKERFILE = Path(__file__).resolve().parents[1] / "packaging" / "Dockerfile"
SECRET = "tok-secret-0123456789abcdefghijklmnopqrstuvwxyz"
INVITE = '{"kind": "sksidekick-invite", "version": 1, "token": "%s"}\n' % SECRET


@pytest.fixture
def box(tmp_path: Path) -> dict[str, Path]:
    d = {"data": tmp_path / "data", "env": tmp_path / "env", "mnt": tmp_path / "mnt"}
    for p in d.values():
        p.mkdir()
    d["home"] = d["data"] / "sidekick"
    d["src"] = d["mnt"] / "invite"
    d["src"].write_text(INVITE)
    d["src"].chmod(0o600)
    return d


def run(box: dict[str, Path], hermes_can_read: bool, invite: Path | str | None = None) -> subprocess.CompletedProcess:
    env = {
        "PATH": os.environ["PATH"],
        "SKSIDEKICK_HOME": str(box["home"]),
        "SIDEKICK_DATA": str(box["data"]),
        "SIDEKICK_ENVDIR": str(box["env"]),
        "SIDEKICK_INVITE_OWNER": f"{os.getuid()}:{os.getgid()}",
        "SIDEKICK_AS_HERMES": "env" if hermes_can_read else "false",
    }
    src = box["src"] if invite is None else invite
    if src != "":
        env["SKSIDEKICK_INVITE"] = str(src)
    return subprocess.run(["sh", str(SCRIPT)], env=env, capture_output=True, text=True, check=True)


def test_unreadable_invite_is_copied_private_and_setup_points_at_it(box):
    r = run(box, hermes_can_read=False)
    copy = box["data"] / "invite.json"
    assert copy.read_text() == INVITE
    assert stat.S_IMODE(copy.stat().st_mode) == 0o600
    assert copy.stat().st_uid == os.getuid()
    assert (box["env"] / "SKSIDEKICK_INVITE").read_text() == str(copy)
    assert box["src"].read_text() == INVITE  # the mounted original is left alone
    assert SECRET not in r.stdout + r.stderr
    assert "private copy" in r.stdout
    assert not list(box["data"].glob(".invite.*"))  # no temp file left behind


def test_copy_replaces_a_symlink_instead_of_writing_through_it(box, tmp_path):
    target = tmp_path / "elsewhere"
    target.write_text("keep me")
    (box["data"] / "invite.json").symlink_to(target)
    run(box, hermes_can_read=False)
    assert not (box["data"] / "invite.json").is_symlink()
    assert (box["data"] / "invite.json").read_text() == INVITE
    assert target.read_text() == "keep me"


def test_readable_invite_is_used_where_it_is(box):
    """The hand-copied layout in use today: /opt/data/invite.json owned by hermes, mode 0600."""
    hand = box["data"] / "invite.json"
    hand.write_text(INVITE)
    hand.chmod(0o600)
    r = run(box, hermes_can_read=True, invite=hand)
    assert hand.read_text() == INVITE
    assert not list(box["env"].iterdir())
    assert r.stdout == ""


def test_readable_mount_is_not_copied(box):
    run(box, hermes_can_read=True)
    assert not (box["data"] / "invite.json").exists()
    assert not list(box["env"].iterdir())


@pytest.mark.parametrize("case", ["unset", "missing", "set_up", "in_volume", "symlink"])
def test_nothing_is_copied(box, case):
    invite: Path | str | None = None
    if case == "unset":
        invite = ""
    elif case == "missing":
        invite = box["mnt"] / "nope"
    elif case == "set_up":
        (box["home"] / "state").mkdir(parents=True)
        (box["home"] / "state" / "instance.json").write_text("{}")
    elif case == "in_volume":
        invite = box["data"] / "other.json"
        invite.write_text(INVITE)
    elif case == "symlink":
        invite = box["mnt"] / "link"
        invite.symlink_to(box["src"])
    r = run(box, hermes_can_read=False, invite=invite)
    assert not (box["data"] / "invite.json").exists()
    assert not list(box["env"].iterdir())
    assert SECRET not in r.stdout + r.stderr


def test_the_image_runs_it_as_root_before_the_drop():
    """A cont-init step runs as root after the base image's own (01, 015, 02) and before the command."""
    assert CONT_INIT.read_text().startswith("#!/command/with-contenv sh\n")
    assert "exec /usr/local/bin/sidekick-invite" in CONT_INIT.read_text()
    df = DOCKERFILE.read_text()
    assert "packaging/sidekick-invite.sh /usr/local/bin/sidekick-invite" in df
    assert "packaging/cont-init.d/03-sksidekick-invite /etc/cont-init.d/03-sksidekick-invite" in df
