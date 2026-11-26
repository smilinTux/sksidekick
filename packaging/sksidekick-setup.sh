#!/usr/bin/env bash
# sksidekick-setup: the first-run setup page for a plain install (install.sh renders @APP@ and @HOME@).
#   sksidekick-setup [--headless]
#   sksidekick-setup --start-over [--yes]   throw away an unfinished setup (never a live sidekick)
# Runs setup with the service's own environment (packaging/sksidekick.service and
# io.skworld.sksidekick.plist, plus the HERMES_HOME sidekick-start exports), so `hermes config set` finds
# this install's Hermes and the first step seeds from this install's Hermes seed. tests/test_packaging_env.py
# keeps this file and the two service files equal. Safe to run again: setup resumes where it stopped.
# The address is always printed; without --headless it is also opened in a browser when there is one.
# Port 0: any free loopback port (8765 may be taken on this computer).
set -eu
app="@APP@"
home="@HOME@"
headless=""
start_over=""
yes=""  # not an array: macOS bash 3.2 with set -u rejects an empty one
while [ $# -gt 0 ]; do
    case "$1" in
        --headless) headless=1 ;;
        --start-over) start_over=1 ;;
        --yes) yes=1 ;;
        -h|--help) sed -n 2,9p "$0"; exit 0 ;;
        *) echo "sksidekick-setup: unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done
if [ -n "$start_over" ]; then
    exec "$app/bin/sksidekick" --home "$home" setup --start-over ${yes:+--yes}
fi
[ -z "$yes" ] || { echo "sksidekick-setup: --yes goes with --start-over" >&2; exit 2; }

if [ "$(uname -s)" = Darwin ]; then svc_path="$app/bin:/usr/bin:/bin:/usr/sbin:/sbin"; else svc_path="$app/bin:/usr/local/bin:/usr/bin:/bin"; fi

if [ -f "$home/state/instance.json" ]; then
    echo "Your sidekick is already set up ($home). Check it with: \"$app/bin/sksidekick\" --home \"$home\" status"
    exit 0
fi

open_url() {
    echo "Setup page (open it in a browser on this computer): $1"
    [ -n "$headless" ] && return 0
    if [ "$(uname -s)" = Darwin ]; then
        open "$1" >/dev/null 2>&1 || true
    elif [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && command -v xdg-open >/dev/null; then
        xdg-open "$1" >/dev/null 2>&1 || true
    fi
}

echo "Setup runs here until you finish it in the browser. Ctrl-C stops it; run \"$app/bin/sksidekick-setup\" again to go on (add --start-over to begin again)."
set +e
env PATH="$svc_path" SKSIDEKICK_HOME="$home" SIDEKICK_SEED="$app/hermes-seed" SIDEKICK_BIN="$app/sksidekick/bin" \
    SIDEKICK_SKMEMORY_JSON="$app/skmemory.json" HERMES_HOME="$home/hermes" \
    "$app/bin/sksidekick" --home "$home" setup --port 0 | while IFS= read -r line; do
    case "$line" in
        "Open http"*) open_url "${line#Open }" ;;
        *) printf '%s\n' "$line" ;;
    esac
done
rc="${PIPESTATUS[0]}"
set -e
exit "$rc"
