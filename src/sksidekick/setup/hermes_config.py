"""Write the model choice through the Hermes CLI; secrets go to hermes/.env, never to a command line."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from sksidekick.home import Home
from sksidekick.setup import state

KEY_VAR = "CUSTOM_API_KEY"


class HermesError(RuntimeError):
    """`hermes` could not be run or refused a setting. The message never carries command output."""


def config_set(home: Home, key: str, value: str) -> None:
    env = {**os.environ, "HERMES_HOME": str(home.hermes_dir)}
    try:
        subprocess.run(
            ["hermes", "config", "set", key, value],
            env=env, check=True, capture_output=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        raise HermesError(f"could not run `hermes config set {key}`") from None


def _env_path(home: Home) -> Path:
    return state.ensure_hermes_dir(home) / ".env"


def _rewrite_envs(home: Home, changes: dict[str, str | None]) -> None:
    """Atomically rewrite hermes/.env once: per key, replace or drop its line, keep the rest."""
    path = _env_path(home)
    lines = path.read_text().splitlines() if path.exists() else []
    for key, value in changes.items():
        kept = [ln for ln in lines if ln.partition("=")[0].strip() != key]
        if value is not None:
            i = next((n for n, ln in enumerate(lines) if ln.partition("=")[0].strip() == key), len(kept))
            kept.insert(min(i, len(kept)), f"{key}={value}")
        lines = kept
    _atomic_private(path, "".join(ln + "\n" for ln in lines).encode())


def _rewrite_env(home: Home, key: str, value: str | None) -> None:
    _rewrite_envs(home, {key: value})


def _check_env(key: str, value: str) -> None:
    if any(c in value for c in "\n\r\0") or any(c in key for c in "\n\r\0=") or not key:
        raise ValueError("value may not contain a newline or NUL")


def set_env(home: Home, key: str, value: str) -> None:
    _check_env(key, value)
    _rewrite_env(home, key, value)


def set_envs(home: Home, values: dict[str, str]) -> None:
    """Set several keys in one atomic rewrite; every key and value is checked before anything is written."""
    for k, v in values.items():
        _check_env(k, v)
    _rewrite_envs(home, dict(values))


def env_keys(home: Home) -> set[str]:
    """Keys with a non-empty value in hermes/.env. Read only: never creates the file or directory."""
    try:
        text = (home.hermes_dir / ".env").read_text()
    except OSError:
        return set()
    return {k.strip() for k, _, v in (ln.partition("=") for ln in text.splitlines()) if v.strip()}


def _atomic_private(path: Path, data: bytes) -> None:
    """Write via <name>.tmp (0600) and rename. A failed write removes the .tmp: it may hold a secret."""
    tmp = path.with_name(path.name + ".tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)  # gone already after the replace


def write_private(path: Path, data: bytes) -> None:
    _atomic_private(path, data)


def snapshot(paths: list[Path]) -> dict[Path, bytes | None]:
    return {p: (p.read_bytes() if p.exists() else None) for p in paths}


def restore(snap: dict[Path, bytes | None]) -> None:
    for p, data in snap.items():
        try:
            if data is None:
                p.unlink(missing_ok=True)
            else:
                write_private(p, data)
        except OSError:
            pass  # never let a restore failure replace the original error


def set_model(home: Home, *, base_url: str, model: str, api_key: str | None) -> None:
    """All or nothing as far as practical: config first, .env last, both files restored byte for byte on failure."""
    state.ensure_hermes_dir(home)
    snap = snapshot([home.hermes_dir / "config.yaml", _env_path(home)])
    try:
        config_set(home, "model.provider", "custom")
        config_set(home, "model.base_url", base_url)
        config_set(home, "model.default", model)
        if api_key:
            set_env(home, KEY_VAR, api_key)
        else:
            _rewrite_env(home, KEY_VAR, None)  # a key from an earlier choice must not outlive it
    except BaseException:
        restore(snap)
        raise
