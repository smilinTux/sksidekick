import hashlib
import json
import os
import re

import pytest

from sksidekick.seats import registry


def cred(client="sk-aaaaaaaaaaaa", rev="r1", token="t1", revoked=False):
    return {"agent_id": "sidekick", "client_id": client,
            "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
            "credential_revision": rev, "revoked": revoked, "expires_at": "2030-01-01T00:00:00Z"}


def test_upsert_creates_0600_and_matches_gateway_schema(tmp_path):
    p = tmp_path / "clients.json"
    registry.upsert(p, cred())
    registry.upsert(p, cred("sk-bbbbbbbbbbbb", token="t2"))
    assert (p.stat().st_mode & 0o777) == 0o600
    doc = json.loads(p.read_text())
    assert doc["schema_version"] == 1
    assert re.match(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$", doc["revision"])
    hashes = [c["token_sha256"] for c in doc["credentials"]]
    assert len(set(hashes)) == len(hashes) == 2
    for c in doc["credentials"]:
        assert re.match(r"^[a-z0-9][a-z0-9._:-]{0,127}$", c["agent_id"])
        assert re.match(r"^[a-z0-9][a-z0-9._:-]{0,127}$", c["client_id"])
        assert c["revoked"] is False


def test_registry_keeps_owner_and_mode(tmp_path):
    p = tmp_path / "clients.json"
    registry.upsert(p, cred())
    os.chmod(p, 0o640)
    before = p.stat()
    registry.upsert(p, cred(token="t9", rev="r2"))
    registry.revoke(p, "sk-aaaaaaaaaaaa")
    after = p.stat()
    assert (after.st_mode & 0o777) == 0o640
    assert (after.st_uid, after.st_gid) == (before.st_uid, before.st_gid)
    assert not list(tmp_path.glob(".clients.json.*"))


def test_revision_changes_and_same_tuple_replaced(tmp_path):
    p = tmp_path / "clients.json"
    r1 = registry.upsert(p, cred())
    r2 = registry.upsert(p, cred(token="other"))
    assert r1 != r2
    assert len(json.loads(p.read_text())["credentials"]) == 1


def test_duplicate_hash_and_bad_fields_refused(tmp_path):
    p = tmp_path / "clients.json"
    registry.upsert(p, cred())
    with pytest.raises(registry.RegistryError):
        registry.upsert(p, cred("sk-bbbbbbbbbbbb"))  # same token
    with pytest.raises(registry.RegistryError):
        registry.upsert(p, cred("SK-UPPER", token="x"))
    with pytest.raises(registry.RegistryError):
        registry.upsert(p, {**cred(token="y"), "expires_at": "tomorrow"})


def test_revoke_unknown_client_and_corrupt_file(tmp_path):
    p = tmp_path / "clients.json"
    registry.upsert(p, cred())
    with pytest.raises(registry.RegistryError):
        registry.revoke(p, "sk-nope")
    p.write_text("{not json")
    with pytest.raises(registry.RegistryError):
        registry.upsert(p, cred(token="z"))


def test_ownership_loss_refused_and_original_untouched(tmp_path, monkeypatch):
    import types

    p = tmp_path / "clients.json"
    registry.upsert(p, cred())
    before = p.read_bytes()
    real = os.fstat

    def other_owner(fd):
        st = real(fd)
        return types.SimpleNamespace(st_uid=st.st_uid + 1, st_gid=st.st_gid)

    def denied(*a):
        raise PermissionError

    monkeypatch.setattr(os, "fchown", denied)
    monkeypatch.setattr(registry.os, "fstat", other_owner)
    with pytest.raises(registry.RegistryError, match="run this as the gateway user"):
        registry.upsert(p, cred(token="t2"))
    assert p.read_bytes() == before
    assert not list(tmp_path.glob(".clients.json.*"))
