#!/usr/bin/env bash
# CI check of packaging/install.sh on a clean macOS or Linux runner.
# No pipefail: checks pipe into grep -q (see tests/image/test_image.sh).
set -eu
repo="$(cd "$(dirname "$0")/../.." && pwd)"
H="${RUNNER_TEMP:-$(mktemp -d)}/sk home"   # a space on purpose: macOS homes live under "Application Support"
fail() { echo "FAIL: $*" >&2; exit 1; }
ok() { echo "ok: $*"; }

if [ "$(uname -s)" = Darwin ]; then app="$HOME/Library/Application Support/sksidekick-app"; else app="${XDG_DATA_HOME:-$HOME/.local/share}/sksidekick-app"; fi

out="$(bash "$repo/packaging/install.sh" --home "$H" 2>&1)" || { printf '%s\n' "$out" | tail -60; fail "install.sh failed"; }
printf '%s\n' "$out" | tail -15
phrase="$(printf '%s\n' "$out" | grep -oE '([0-9A-HJKMNP-TV-Z]{5}-){7}[0-9A-HJKMNP-TV-Z]{5}' | head -1)"
[ -n "$phrase" ] || fail "no recovery phrase shown"
ok "installed, and the recovery phrase was shown"

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

grep -rqF "$phrase" "$app" "$H" 2>/dev/null && fail "the recovery phrase was written to disk"
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
