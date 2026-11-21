#!/bin/sh
# Run a hermes command against the sidekick's own Hermes home (plain `hermes` uses /opt/data).
set -e
export HERMES_HOME="${SKSIDEKICK_HOME:-/opt/data/sidekick}/hermes"
exec hermes "$@"
