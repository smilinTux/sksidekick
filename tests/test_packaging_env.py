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
