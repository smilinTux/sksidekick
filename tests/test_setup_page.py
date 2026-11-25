"""Static checks of the first-run page: it loads nothing from the internet and works under a path prefix."""

from __future__ import annotations

import re
from pathlib import Path

from sksidekick.setup import server

STATIC = Path(server.__file__).parent / "static"
TEXT = (".html", ".js", ".css")


def _text_files() -> list[Path]:
    return sorted(p for p in STATIC.rglob("*") if p.suffix in TEXT)


def _read(name: str) -> str:
    return (STATIC / name).read_text()


def test_page_files_exist():
    names = {p.name for p in _text_files()}
    assert {"index.html", "setup.js", "setup.css"} <= names


def test_page_has_no_external_urls():
    for f in _text_files():
        text = re.sub(r'placeholder="[^"]*"', "", f.read_text())  # the user's own input hints
        assert "http://" not in text and "https://" not in text, f.name
        assert not re.search(r"(src|href)\s*=\s*[\"']//", text), f.name
        assert "@import" not in text and "url(" not in text, f.name
    html = _read("index.html")
    for src in re.findall(r"<script[^>]*\bsrc=\"([^\"]+)\"", html):
        assert src.startswith("static/"), src


def test_page_uses_relative_urls():
    for f in _text_files():
        text = f.read_text()
        assert not re.search(r"(href|src)=\"/", text), f.name
        assert not re.search(r"fetch\(\s*[\"'`]/", text), f.name
    js = _read("setup.js")
    for arg in re.findall(r"fetch\(\s*([^,)]+)", js):
        assert not arg.strip().startswith(("\"/", "'/", "`/")), arg
    for path in re.findall(r"(?<![.\w])(?:get|post|send)\(\s*\"([^\"]+)\"", js):
        assert not path.startswith("/") and path.startswith("api/"), path


def test_csp_compliant_no_inline_script_or_style():
    """The server sends default-src 'self': inline scripts, styles and handlers would be blocked."""
    html = _read("index.html")
    for m in re.finditer(r"<script\b([^>]*)>(.*?)</script>", html, re.S):
        assert "src=" in m.group(1) and not m.group(2).strip()
    assert "<style" not in html
    assert not re.search(r"\sstyle\s*=", html)
    assert not re.search(r"\son[a-z]+\s*=", html)
    js = _read("setup.js")
    assert "eval(" not in js and "new Function" not in js
    assert "innerHTML" not in js  # text only: no markup built from strings


def test_every_id_the_js_queries_exists():
    html = _read("index.html")
    ids = set(re.findall(r"\bid=\"([^\"]+)\"", html))
    wanted = set(re.findall(r"\$\(\"([A-Za-z0-9_-]+)\"\)", _read("setup.js")))
    assert wanted, "setup.js queries no ids"
    assert wanted <= ids, sorted(wanted - ids)
    for label_for in re.findall(r"<label[^>]*\bfor=\"([^\"]+)\"", html):
        assert label_for in ids, label_for


def test_post_always_sends_the_token_header():
    js = _read("setup.js")
    fetches = [m.start() for m in re.finditer(r"\bfetch\(", js)]
    assert len(fetches) == 2, "one GET helper and one POST helper"
    posts = [m.start() for m in re.finditer(r"\"POST\"", js)]
    assert posts, "no POST helper"
    for at in posts:
        window = js[max(0, at - 300): at + 300]
        assert "\"X-Setup-Token\": token" in window
    assert "localStorage" not in js and "document.cookie" not in js
    assert "history.replaceState" in js and "location.search" in js


def test_token_kept_per_tab_and_nothing_else_stored():
    """The token survives a reload in sessionStorage (per tab); every access is guarded; the phrase never goes there."""
    js = _read("setup.js")
    lines = [ln for ln in js.splitlines() if "sessionStorage" in ln and not ln.lstrip().startswith("//")]
    assert lines, "the token is not kept for a reload"
    assert all("try {" in ln and "catch" in ln for ln in lines), lines
    keys = re.findall(r"\bstore\(\s*([A-Za-z_]+)", js)
    assert keys and set(keys) <= {"TOKEN_KEY", "OPEN_KEY"}, keys
    assert re.search(r"get\(\"t\"\)\s*\|\|\s*stored\(TOKEN_KEY\)", js), "?t= first, then sessionStorage"


def test_phrase_never_logged_or_stored_by_the_page():
    js = _read("setup.js")
    assert "console." not in js


def test_phrase_sentence_verbatim():
    want = ("Write these 8 groups down on paper. This is the only way to restore your sidekick if this computer "
            "is lost. We cannot recover it for you.")
    assert want in re.sub(r"\s+", " ", _read("index.html"))


def test_no_dashes_in_page_files():
    for f in _text_files():
        assert not re.search(r"[\u2013\u2014]", f.read_text()), f.name


def test_page_served_with_its_assets(bare_home):
    import threading

    from test_setup_server import TOKEN, call

    srv = server.make_server(bare_home, "127.0.0.1", 0, TOKEN)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        port = srv.server_address[1]
        status, headers, body = call(port, "GET", f"/?t={TOKEN}")
        assert status == 200 and headers["Content-Type"].startswith("text/html")
        for ref in re.findall(r"(?:src|href)=\"(static/[^\"]+)\"", body.decode()):
            s, h, _ = call(port, "GET", "/" + ref, cookie=TOKEN)
            assert s == 200, ref
        assert call(port, "GET", "/static/setup.js", cookie=TOKEN)[1]["Content-Type"] == "text/javascript"
    finally:
        srv.shutdown()
        srv.server_close()
