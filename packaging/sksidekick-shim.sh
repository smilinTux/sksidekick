#!/bin/sh
# `docker exec` runs as root; the sidekick's files must belong to the hermes user (the same
# rule as the fork's own hermes shim), so drop to it before running sksidekick.
set -e
REAL=/opt/sksidekick/bin/sksidekick
if [ "$(id -u)" = 0 ] && [ -x /command/s6-setuidgid ]; then
    export HOME=/opt/data
    exec /command/s6-setuidgid hermes "$REAL" "$@"
fi
exec "$REAL" "$@"
