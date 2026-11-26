"""Remove login secrets from Hermes' config files when an export leaves without credentials.

Only line-based edits, so a user can still read and fix the files by hand:

* ``hermes/.env``: every ``KEY=value`` (optionally ``export KEY=value``) becomes ``KEY=__REMOVED__``.
* ``hermes/config.yaml``: any mapping key that looks like a secret (token, secret, password, api key)
  gets the value ``__REMOVED__``; a block or nested value under it is dropped with it.
"""

from __future__ import annotations

import re

REMOVED = "__REMOVED__"
SECRET_KEY = re.compile(r"(?i)(token|secret|passw(or)?d|api[-_]?key)")
_ENV_LINE = re.compile(r"^(?P<lead>\s*(?:export\s+)?)(?P<key>[A-Za-z_][A-Za-z0-9_]*)=(?P<val>.*)$")
_YAML_KEY = re.compile(r"^(?P<lead>\s*(?:-\s+)?)(?P<key>[\"']?[A-Za-z0-9_.-]+[\"']?)\s*:(?P<rest>(\s.*)?)$")


def redact_env(text: str) -> tuple[str, list[str]]:
    out, found = [], []
    for line in text.splitlines(keepends=True):
        m = _ENV_LINE.match(line.rstrip("\r\n"))
        if m and m.group("val").strip():
            out.append(f"{m.group('lead')}{m.group('key')}={REMOVED}\n")
            found.append(m.group("key"))
        else:
            out.append(line)
    return "".join(out), found


def redact_config(text: str) -> tuple[str, list[str]]:
    out, found = [], []
    skip_deeper_than: int | None = None
    for line in text.splitlines(keepends=True):
        bare = line.rstrip("\r\n")
        if skip_deeper_than is not None:
            indent = len(bare) - len(bare.lstrip())
            if not bare.strip() or indent > skip_deeper_than:
                continue
            skip_deeper_than = None
        m = _YAML_KEY.match(bare)
        if m and not bare.lstrip().startswith("#") and SECRET_KEY.search(m.group("key")):
            rest = m.group("rest").strip()
            if rest.startswith("#"):
                out.append(line)  # only a comment after the colon: nothing to hide on this line
                continue
            out.append(f"{m.group('lead')}{m.group('key')}: {REMOVED}\n")
            found.append(m.group("key").strip("\"'"))
            skip_deeper_than = len(m.group("lead"))  # drop a block scalar or nested mapping under it
        else:
            out.append(line)
    return "".join(out), found


REDACTORS = {"hermes/.env": redact_env, "hermes/config.yaml": redact_config}
