import json
import os

import pytest

from sksidekick.seats.record import Invite, Seats, check_gateway_url, write_private


def make(url="https://gw.example.com/v1"):
    return Invite(url, "sidekick", "sk-0123456789ab", "cr-1", "secret-token", "2030-01-01T00:00:00Z")


def test_invite_round_trip_and_shape():
    inv = make()
    d = json.loads(inv.to_json())
    assert d["kind"] == "sksidekick-invite" and d["version"] == 1
    assert Invite.from_json(inv.to_json()) == inv


@pytest.mark.parametrize("text", ["nope", "[]", json.dumps({"kind": "other", "version": 1}),
                                  json.dumps({"kind": "sksidekick-invite", "version": 2})])
def test_bad_invites_raise_value_error(text):
    with pytest.raises(ValueError):
        Invite.from_json(text)


def test_invite_missing_field_raises():
    d = json.loads(make().to_json())
    del d["token"]
    with pytest.raises(ValueError, match="token"):
        Invite.from_json(json.dumps(d))


@pytest.mark.parametrize("url", ["http://127.0.0.1:18790/v1", "http://10.0.0.5/v1",
                                 "http://localhost:1/v1", "http://169.254.1.1/v1", "https://example.com/v1"])
def test_url_accepted(url):
    assert check_gateway_url(url) == url


@pytest.mark.parametrize("url", ["http://example.com/v1", "http://8.8.8.8/v1", "ftp://10.0.0.5", "gw"])
def test_url_refused(url):
    with pytest.raises(ValueError):
        check_gateway_url(url)
    with pytest.raises(ValueError):
        Invite.from_json(make(url).to_json()) if url.startswith("http") else check_gateway_url(url)


def test_write_private_mode_and_no_overwrite(tmp_path):
    p = tmp_path / "i.json"
    write_private(p, "x")
    assert (p.stat().st_mode & 0o777) == 0o600
    with pytest.raises(FileExistsError):
        write_private(p, "y")


def test_seats_dir_modes(tmp_path):
    s = Seats(tmp_path / "seats")
    s.init("a")
    assert (s.root.stat().st_mode & 0o777) == 0o700
    assert (s.file.stat().st_mode & 0o777) == 0o600
    assert s.load() == {"estate": "a", "seats": []}
