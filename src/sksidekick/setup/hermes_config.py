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


def _config_set(home: Home, key: str, value: str) -> None:
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


def _rewrite_env(home: Home, key: str, value: str | None) -> None:
    """Atomically rewrite hermes/.env: replace or drop `key`'s line, keep the rest."""
    path = _env_path(home)
    lines = path.read_text().splitlines() if path.exists() else []
    kept = [ln for ln in lines if ln.partition("=")[0].strip() != key]
    if value is not None:
        i = next((n for n, ln in enumerate(lines) if ln.partition("=")[0].strip() == key), len(kept))
        kept.insert(min(i, len(kept)), f"{key}={value}")
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("".join(ln + "\n" for ln in kept))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def set_env(home: Home, key: str, value: str) -> None:
    if any(c in value for c in "\n\r\0") or any(c in key for c in "\n\r\0="):
        raise ValueError("value may not contain a newline or NUL")
    _rewrite_env(home, key, value)


def _config_get(home: Home, key: str) -> str:
    env = {**os.environ, "HERMES_HOME": str(home.hermes_dir)}
    try:
        r = subprocess.run(["hermes", "config", "get", key], env=env, check=True, capture_output=True, timeout=60, text=True)
    except (OSError, subprocess.SubprocessError):
        raise HermesError(f"could not run `hermes config get {key}`") from None
    return r.stdout.strip()


def set_model(home: Home, *, base_url: str, model: str, api_key: str | None) -> None:
    """All or nothing as far as practical: config first (restored on failure), .env last."""
    state.ensure_hermes_dir(home)
    new = {"model.provider": "custom", "model.base_url": base_url, "model.default": model}
    before = {k: _config_get(home, k) for k in new}
    env_path = _env_path(home)
    env_before = env_path.read_bytes() if env_path.exists() else None
    done: list[str] = []
    try:
        for k, v in new.items():
            _config_set(home, k, v)
            done.append(k)
        if api_key:
            set_env(home, KEY_VAR, api_key)
        else:
            _rewrite_env(home, KEY_VAR, None)  # a key from an earlier choice must not outlive it
    except BaseException:
        for k in done + [k for k in new if k not in done][:1]:  # the failed set may have half applied
            try:
                _config_set(home, k, before[k])
            except HermesError:
                pass
        if env_before is None:
            env_path.unlink(missing_ok=True)
        else:
            tmp = env_path.with_name(env_path.name + ".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(env_before)
            os.replace(tmp, env_path)
        raise
