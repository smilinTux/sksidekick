"""The plain answer a sidekick gives once its shared seat stops working (issue #9).

Hermes falls back to ``sidekick-seat-notice`` when the gateway refuses the seat's credential; the notice
answers with one sentence instead of Hermes' "run `hermes setup`".
"""

from __future__ import annotations

import http.client
import json

import pytest

from sksidekick import seat_notice, serve
from sksidekick.setup import hermes_config, seat
from test_serve import Clock, FakeProc, live  # noqa: F401  (live is a fixture)
from test_setup_seat import SECRET, make_invite, stub  # noqa: F401  (stub is a fixture)


def _seated(home, url="http://127.0.0.1:1/v1", **kw):
    home.root.mkdir(parents=True, exist_ok=True)
    seat.install(home, make_invite(url=url, **kw))
    return home


def _ask(port, method="POST", path="/v1/chat/completions", body=None, token=SECRET):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    c.request(method, path, json.dumps(body).encode() if body is not None else None, headers)
    r = c.getresponse()
    return r.status, r.read().decode()


class Recorder:
    def __init__(self, fail: bool = False):
        self.sets: list[tuple[str, str]] = []
        self.fail = fail

    def __call__(self, home, key, value):
        if self.fail:
            raise hermes_config.HermesError("no hermes")
        self.sets.append((key, value))


@pytest.mark.parametrize("status", [401, 403])
def test_a_refused_seat_says_it_was_removed(bare_home, stub, status):
    gw = stub(status=status)
    _seated(bare_home, url=gw.url)
    assert seat_notice.message(bare_home) == seat_notice.SEAT_REMOVED
    assert gw.seen and gw.seen[0]["x-sk-client-id"] == "sk-0123456789ab"  # asked with the seat's own headers


def test_an_expired_seat_says_it_was_removed_without_asking(bare_home, stub):
    gw = stub(status=200)
    _seated(bare_home, url=gw.url, days=-1)
    assert seat_notice.message(bare_home) == seat_notice.SEAT_REMOVED
    assert not gw.seen


@pytest.mark.parametrize("status", [200, 429, 500])
def test_a_seat_the_gateway_still_takes_says_try_again(bare_home, stub, status):
    gw = stub(status=status, body=b'{"data": []}')
    _seated(bare_home, url=gw.url)
    assert seat_notice.message(bare_home) == seat_notice.GATEWAY_DOWN


def test_the_message_never_says_hermes_setup():
    for text in (seat_notice.SEAT_REMOVED, seat_notice.GATEWAY_DOWN):
        assert "hermes setup" not in text and chr(0x2014) not in text and chr(0x2013) not in text
    assert "new invite" in seat_notice.SEAT_REMOVED and "setup page" in seat_notice.SEAT_REMOVED


def test_no_seat_no_notice(bare_home):
    rec = Recorder()
    n = seat_notice.Notice(bare_home, config_set=rec)
    n.ensure()
    assert n.server is None and rec.sets == []


def test_notice_answers_as_hermes_fallback(bare_home):
    _seated(bare_home)
    rec = Recorder()
    n = seat_notice.Notice(bare_home, config_set=rec, answer=lambda h: seat_notice.SEAT_REMOVED)
    n.ensure()
    try:
        port = n.port
        assert dict(rec.sets) == {
            "providers.sidekick-seat-notice.api": f"http://127.0.0.1:{port}/v1",
            "providers.sidekick-seat-notice.key_env": "SKSIDEKICK_GATEWAY_TOKEN",
            "providers.sidekick-seat-notice.default_model": "seat-notice",
            "fallback_providers": json.dumps([{"provider": "sidekick-seat-notice", "model": "seat-notice"}]),
        }
        status, body = _ask(port, body={"model": "seat-notice", "messages": [{"role": "user", "content": "hi"}]})
        assert status == 200
        assert json.loads(body)["choices"][0]["message"]["content"] == seat_notice.SEAT_REMOVED
        status, body = _ask(port, body={"model": "seat-notice", "stream": True, "messages": []})
        assert status == 200
        chunks = [json.loads(ln[6:]) for ln in body.splitlines() if ln.startswith("data: {")]
        assert "".join(c["choices"][0]["delta"].get("content", "") for c in chunks if c["choices"]) == seat_notice.SEAT_REMOVED
        assert body.rstrip().endswith("data: [DONE]")
        status, body = _ask(port, method="GET", path="/v1/models")
        assert status == 200 and json.loads(body)["data"][0]["id"] == "seat-notice"
        assert _ask(port, token=None, body={})[0] == 401
        assert _ask(port, token="someone-else", body={})[0] == 401
        n.ensure()  # already running: nothing more
        assert len(rec.sets) == 4
    finally:
        n.close()
    assert n.server is None


def test_the_recorded_port_is_reused_without_touching_hermes(bare_home):
    _seated(bare_home)
    first = seat_notice.Notice(bare_home, config_set=Recorder())
    first.ensure()
    port = first.port
    first.close()
    rec = Recorder()
    again = seat_notice.Notice(bare_home, config_set=rec)
    again.ensure()
    try:
        assert again.port == port and rec.sets == []
    finally:
        again.close()


def test_a_taken_port_moves_the_fallback(bare_home):
    _seated(bare_home)
    holder = seat_notice.Notice(bare_home, config_set=Recorder())
    holder.ensure()  # holds the recorded port
    rec = Recorder()
    other = seat_notice.Notice(bare_home, config_set=rec)
    other.ensure()
    try:
        assert other.port != holder.port
        assert ("providers.sidekick-seat-notice.api", f"http://127.0.0.1:{other.port}/v1") in rec.sets
        assert json.loads((bare_home.config_dir / "seat_notice.json").read_text()) == {"port": other.port}
    finally:
        other.close()
        holder.close()


def test_a_failed_config_is_retried_later_not_every_tick(bare_home):
    _seated(bare_home)
    t = {"now": 100.0}
    n = seat_notice.Notice(bare_home, config_set=Recorder(fail=True), clock=lambda: t["now"])
    n.ensure()
    assert n.server is None and not (bare_home.config_dir / "seat_notice.json").exists()
    n.config_set = Recorder()
    n.ensure()
    assert n.server is None  # still inside the retry wait
    t["now"] += seat_notice.RETRY_AFTER
    n.ensure()
    try:
        assert n.server is not None
    finally:
        n.close()


def test_serve_starts_the_notice_and_closes_it(live):
    clock = Clock()
    made = []

    class FakeNotice:
        def __init__(self, home):
            self.ensured = 0
            self.closed = False
            made.append(self)

        def ensure(self):
            self.ensured += 1

        def close(self):
            self.closed = True

    stops = iter([False, False, True])
    rc = serve.serve(live, gateway_argv=["gw"], clock=lambda: clock.mono, wall=lambda: clock.now, sleep=clock.sleep,
                     notify=lambda t: None, spawn=lambda a: FakeProc(a), jitter=lambda: 0.0,
                     stop=lambda: next(stops), notice=FakeNotice)
    assert rc == 0
    assert len(made) == 1 and made[0].ensured == 2 and made[0].closed
