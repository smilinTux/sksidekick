"""sksidekick-setup (which install.sh runs) has the same environment the service gets; keep the copies equal."""

from __future__ import annotations

import re
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "packaging"


def _unit() -> dict[str, str]:
    text = (PKG / "sksidekick.service").read_text()
    return dict(m.groups() for m in re.finditer(r'^Environment="([A-Z_]+)=(.*)"$', text, re.M))


def _plist() -> dict[str, str]:
    text = (PKG / "io.skworld.sksidekick.plist").read_text()
    env = text.split("<key>EnvironmentVariables</key>", 1)[1].split("</dict>", 1)[0]
    return dict(re.findall(r"<key>([A-Z_]+)</key><string>([^<]*)</string>", env))


def _install_setup_env() -> tuple[dict[str, str], str, str]:
    text = (PKG / "sksidekick-setup.sh").read_text()
    mac, linux = re.search(r'Darwin \]; then svc_path="([^"]+)"; else svc_path="([^"]+)"', text).groups()
    call = re.search(r"env (PATH=.*?)\"\$app/bin/sksidekick\" --home \"\$home\" setup", text, re.S).group(1)
    pairs = dict(re.findall(r'([A-Z_]+)="([^"]*)"', call))
    return pairs, mac, linux


def _norm(v: str) -> str:
    return v.replace("@APP@", "$app").replace("@HOME@", "$home")


def test_setup_env_matches_both_services():
    pairs, mac, linux = _install_setup_env()
    for svc, path in ((_unit(), linux), (_plist(), mac)):
        assert _norm(svc["PATH"]) == path
        for k, v in svc.items():
            # SETUP=off only keeps the service from a second setup; SETUP_CMD only names the wrapper in its message
            if k not in ("PATH", "SKSIDEKICK_SETUP", "SIDEKICK_SETUP_CMD"):
                assert pairs.get(k) == _norm(v), k
    assert pairs["HERMES_HOME"] == "$home/hermes"  # as sidekick-start exports it
    assert _unit()["SKSIDEKICK_SETUP"] == "off" and _plist()["SKSIDEKICK_SETUP"] == "off"


def test_install_uses_the_wrapper_and_points_at_it():
    install = (PKG / "install.sh").read_text()
    assert 'render "$here/sksidekick-setup.sh" > "$app/bin/sksidekick-setup"' in install
    assert '"$app/bin/sksidekick-setup" $flag' in install
    assert " setup --port" not in install  # no second copy of the setup call
    assert "sksidekick-setup" in install.split('die "setup stopped', 1)[1].split("\n", 1)[0]
    assert _unit()["SIDEKICK_SETUP_CMD"] == "@APP@/bin/sksidekick-setup" == _plist()["SIDEKICK_SETUP_CMD"]


def _rendered_wrapper(tmp_path: Path) -> tuple[Path, Path, Path]:
    """sksidekick-setup rendered as install.sh does, into an app dir with a space (as on macOS)."""
    import os

    app, home = tmp_path / "Application Support" / "sksidekick-app", tmp_path / "sk home"
    (app / "bin").mkdir(parents=True)
    fake = app / "bin" / "sksidekick"
    fake.write_text('#!/bin/sh\nprintf "%s|" "$@"\n')
    fake.chmod(0o755)
    text = (PKG / "sksidekick-setup.sh").read_text().replace("@APP@", str(app)).replace("@HOME@", str(home))
    w = app / "bin" / "sksidekick-setup"
    w.write_text(text)
    os.chmod(w, 0o755)
    return w, app, home


def test_wrapper_passes_start_over_through(tmp_path):
    import subprocess

    w, app, home = _rendered_wrapper(tmp_path)
    for args, want in ((["--start-over"], ["--home", str(home), "setup", "--start-over"]),
                       (["--start-over", "--yes"], ["--home", str(home), "setup", "--start-over", "--yes"]),
                       (["--yes", "--start-over"], ["--home", str(home), "setup", "--start-over", "--yes"])):
        r = subprocess.run(["bash", str(w), *args], capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, r.stderr
        assert r.stdout.split("|")[:-1] == want
    r = subprocess.run(["bash", str(w), "--yes"], capture_output=True, text=True, timeout=30)
    assert r.returncode == 2 and "--start-over" in r.stderr


def test_suggested_commands_are_full_quoted_paths():
    """~/.local/bin is not on PATH on macOS, and the app path holds a space: name commands by full path, quoted."""
    start = (PKG / "sidekick-start.sh").read_text()
    assert '\\"$SIDEKICK_SETUP_CMD\\"' in start and " $SIDEKICK_SETUP_CMD " not in start
    install = (PKG / "install.sh").read_text()
    for line in install.splitlines():
        if line.lstrip().startswith(("die ", "say ", "echo ")) or "|| die " in line:
            for cmd in ("sksidekick-setup", "sksidekick --home"):
                if cmd in line:
                    assert f'\\"$app/bin/{cmd.split()[0]}\\"' in line, line
    wrapper = (PKG / "sksidekick-setup.sh").read_text()
    assert 'run \\"$app/bin/sksidekick-setup\\" again' in wrapper


def test_wrapper_forwards_invite_flags(tmp_path):
    import subprocess

    w, app, home = _rendered_wrapper(tmp_path)
    (app / "bin" / "sksidekick").write_text('#!/bin/sh\nprintf "%s|" "$@"; echo\n')  # the wrapper reads lines
    for args, extra in ((["--invite", "/x y/inv.json"], ["--invite", "/x y/inv.json"]),
                        (["--invite", "/x y/inv.json", "--keep-invite"], ["--invite", "/x y/inv.json", "--keep-invite"])):
        r = subprocess.run(["bash", str(w), *args], capture_output=True, text=True, timeout=30)
        got = r.stdout.strip().splitlines()[-1].split("|")[:-1]
        assert r.returncode == 0, r.stderr
        assert got[:3] == ["--home", str(home), "setup"] and got[-len(extra):] == extra, got
    # a live home still takes an invite (a rotation)
    (home / "state").mkdir(parents=True)
    (home / "state" / "instance.json").write_text("{}")
    r = subprocess.run(["bash", str(w), "--invite", "/i.json"], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and "--invite|/i.json|" in r.stdout and "already set up" not in r.stdout
    r = subprocess.run(["bash", str(w), "--keep-invite"], capture_output=True, text=True, timeout=30)
    assert r.returncode == 2 and "--invite" in r.stderr


def test_install_forwards_invite_flags_to_setup():
    install = (PKG / "install.sh").read_text()
    assert '"$app/bin/sksidekick-setup" $flag ${invite:+--invite "$invite"} ${keep:+--keep-invite}' in install
    assert "--invite FILE [--keep-invite]" in install.split("set -eu", 1)[0]  # in the usage lines


def test_install_refuses_invite_misuse_before_installing(tmp_path):
    import subprocess

    env = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}
    for args, msg in ((["--keep-invite"], "--keep-invite goes with --invite"),
                      (["--invite"], "--invite needs a file"),
                      (["--invite", "/i.json", "--no-init"], "--invite does not go with --no-init or --init"),
                      (["--invite", "/i.json", "--init"], "--invite does not go with --no-init or --init")):
        r = subprocess.run(["bash", str(PKG / "install.sh"), *args], capture_output=True, text=True, timeout=30, env=env)
        assert r.returncode != 0 and msg in r.stderr, (args, r.stderr)
    assert not (tmp_path / ".local").exists() and not (tmp_path / "Library").exists()


def test_start_on_a_live_container_says_the_invite_was_not_used(tmp_path):
    import subprocess

    home, bin_ = tmp_path / "home", tmp_path / "bin"
    (home / "state").mkdir(parents=True)
    (home / "state" / "instance.json").write_text('{"status": "live"}')
    bin_.mkdir()
    for name in ("sksidekick", "python"):  # serve and the seed/live checks: all succeed
        (bin_ / name).write_text("#!/bin/sh\nexit 0\n")
        (bin_ / name).chmod(0o755)
    env = {"PATH": "/usr/bin:/bin", "SKSIDEKICK_HOME": str(home), "SIDEKICK_BIN": str(bin_),
           "SIDEKICK_SEED": str(tmp_path / "seed"), "SKSIDEKICK_INVITE": "/run/invite"}
    r = subprocess.run(["sh", str(PKG / "sidekick-start.sh")], capture_output=True, text=True, timeout=30, env=env)
    assert r.returncode == 0, r.stderr
    lines = [ln for ln in (r.stdout + r.stderr).splitlines() if "invite" in ln]
    assert len(lines) == 1 and "already set up" in lines[0] and "not used" in lines[0], lines
    assert "sksidekick setup --invite" in lines[0]  # the rotation path for a new invite for the same seat
    env.pop("SKSIDEKICK_INVITE")
    r = subprocess.run(["sh", str(PKG / "sidekick-start.sh")], capture_output=True, text=True, timeout=30, env=env)
    assert "invite" not in r.stdout + r.stderr
