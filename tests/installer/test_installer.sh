#!/usr/bin/env bash
# CI check of packaging/install.sh on a clean macOS or Linux runner.
# No pipefail: checks pipe into grep -q (see tests/image/test_image.sh).
set -eu
repo="$(cd "$(dirname "$0")/../.." && pwd)"
H="${RUNNER_TEMP:-$(mktemp -d)}/sk home"   # a space on purpose: macOS homes live under "Application Support"
fail() { echo "FAIL: $*" >&2; exit 1; }
ok() { echo "ok: $*"; }

if [ "$(uname -s)" = Darwin ]; then app="$HOME/Library/Application Support/sksidekick-app"; else app="${XDG_DATA_HOME:-$HOME/.local/share}/sksidekick-app"; fi

# install.sh runs setup in the foreground until it is finished; walk it over HTTP the way the page would.
work="$(mktemp -d)"
log="$work/install.log"
mkdir -p "$work/usb"
python3 "$repo/tests/image/stub_model.py" 18999 >"$work/stub.log" 2>&1 &
stub=$!
bash "$repo/packaging/install.sh" --home "$H" --setup-headless >"$log" 2>&1 &
inst=$!
trap 'kill "$stub" 2>/dev/null || true; kill "$inst" 2>/dev/null || true; rm -rf "$work"' EXIT
url=""
for _ in $(seq 1200); do  # the install itself takes minutes
    url="$(sed -n 's|^Setup page (open it in a browser on this computer): ||p' "$log" | tail -1)"
    [ -n "$url" ] && break
    kill -0 "$inst" 2>/dev/null || { tail -60 "$log"; fail "install.sh ended before setup started"; }
    sleep 1
done
[ -n "$url" ] || { tail -60 "$log"; fail "install.sh printed no setup address"; }
case "$url" in http://127.0.0.1:*/\?t=*) ;; *) fail "the setup address is not on loopback: ${url%%\?*}";; esac
ok "install.sh started the setup page on loopback (--setup-headless)"
exec 4>&1
phrase="$(SETUP_WALK_BACKUP_DIR="$work/usb" bash "$repo/tests/setup_walk.sh" "${url%%/\?t=*}" "${url##*\?t=}" 3>&1 1>&4)" \
    || { tail -40 "$log"; fail "the setup walk failed"; }
exec 4>&-
[ -n "$phrase" ] || fail "the walk handed back no phrase"
wait "$inst" || { tail -60 "$log"; fail "install.sh failed"; }
tail -15 "$log"
grep -qF "$phrase" "$log" && fail "the recovery phrase was printed by install.sh"
ok "installed and set up through the page"

if [ "$(uname -s)" = Darwin ]; then
    launchctl print "gui/$(id -u)/io.skworld.sksidekick" >/dev/null || fail "launchd agent not loaded"
else
    systemctl --user is-enabled sksidekick.service | grep -qx enabled || fail "systemd unit not enabled"
fi
ok "the service is loaded"

"$HOME/.local/bin/sksidekick" --home "$H" status | grep -q '"status": "live"' || fail "status is not live"
ok "the sidekick is live"

for _ in $(seq 120); do pgrep -f "sksidekick serve" >/dev/null && break; sleep 1; done
pgrep -f "sksidekick serve" >/dev/null || {
    cat "$app/logs/sksidekick.log" 2>/dev/null | tail -30 || true
    journalctl --user -u sksidekick.service -n 30 --no-pager 2>/dev/null || true
    fail "sksidekick serve is not running"
}
ok "the service runs sksidekick serve"
HERMES_HOME="$H/hermes" "$app/bin/hermes" config get memory.provider | grep -qx skmemory || fail "memory.provider is not skmemory"
ok "the sidekick's Hermes home uses the skmemory provider"

grep -rqF "$phrase" "$app" "$H" "$work" 2>/dev/null && fail "the recovery phrase was written to disk"
ok "the phrase is not stored anywhere"

bash "$repo/packaging/install.sh" --uninstall --home "$H"
[ -f "$H/keys/live.identity" ] || fail "uninstall removed the sidekick's data"
[ ! -e "$app" ] || fail "uninstall left the app folder"
if [ "$(uname -s)" = Darwin ]; then
    launchctl print "gui/$(id -u)/io.skworld.sksidekick" >/dev/null 2>&1 && fail "launchd agent still loaded"
else
    systemctl --user is-enabled sksidekick.service >/dev/null 2>&1 && fail "systemd unit still enabled"
fi
sleep 3
pgrep -f "sksidekick serve" >/dev/null && fail "serve still runs after uninstall"
ok "uninstall removed code and service and kept the data"
echo "all installer checks passed"
