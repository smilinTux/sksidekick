#!/bin/sh
# Container command (and the plain install's service). Waits for a sidekick (init or import), gives it its own Hermes home inside the
# sidekick home, then runs `sksidekick serve`. A copy that is not live idles instead of exiting, so a
# restart policy never turns a moved-out copy into a crash loop.
set -eu
home="${SKSIDEKICK_HOME:?}"
seed="${SIDEKICK_SEED:-/opt/data}"      # where Hermes' installer or boot hook seeded config.yaml, .env and SOUL.md
bin="${SIDEKICK_BIN:-/opt/sksidekick/bin}"  # sksidekick's own venv
said=""
while [ ! -f "$home/state/instance.json" ]; do
    if [ -z "$said" ]; then
        echo "No sidekick here yet. Run: docker exec -it <container> sksidekick init   (or sksidekick import ARCHIVE --phrase-stdin)"
        said=1
    fi
    sleep 5
done

h="$home/hermes"
mkdir -p "$h"
chmod 700 "$h"
for f in config.yaml .env SOUL.md; do
    if [ ! -e "$h/$f" ] && [ -e "$seed/$f" ]; then
        cp -p "$seed/$f" "$h/$f"
    fi
done
[ -e "$h/skmemory.json" ] || cp "${SIDEKICK_SKMEMORY_JSON:-/opt/sksidekick/skmemory.json}" "$h/skmemory.json"
export HERMES_HOME="$h"
if [ "$(hermes config get memory.provider 2>/dev/null || true)" != "skmemory" ]; then
    hermes config set memory.provider skmemory >/dev/null
fi

code=0
"$bin/sksidekick" serve || code=$?
if ! "$bin/python" -c 'import json, os, sys; s = json.load(open(os.path.join(sys.argv[1], "state", "instance.json")))["status"]; sys.exit(0 if s == "live" else 1)' "$home"; then
    echo "This copy is not the live sidekick (moved out or retired). It stays stopped. When the new place works: docker exec <container> sksidekick retire --yes"
    exec sleep infinity
fi
exit "$code"
