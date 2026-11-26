#!/usr/bin/env bash
# Plain sksidekick install for macOS and Linux (no container):
#   install.sh [--home PATH] [--no-init] [--uninstall] [--force-hermes-link]
#   --no-init: leave the home empty for `sksidekick import` (moving here); the service waits for it.
# Code goes in the app folder; the sidekick's data (its home) goes elsewhere and is never removed here.
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/.." && pwd)"
# shellcheck source=/dev/null
. "$here/versions.env"

die() { echo "install.sh: $*" >&2; exit 1; }
say() { echo "==> $*"; }

case "$(uname -s)" in
    Darwin)
        app="$HOME/Library/Application Support/sksidekick-app"
        home="$HOME/Library/Application Support/sksidekick"
        plist="$HOME/Library/LaunchAgents/io.skworld.sksidekick.plist" ;;
    Linux)
        data="${XDG_DATA_HOME:-$HOME/.local/share}"
        app="$data/sksidekick-app"
        home="$data/sksidekick"
        unit="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/sksidekick.service" ;;
    *) die "macOS and Linux only" ;;
esac

uninstall=""
force_link=""
no_init=""
while [ $# -gt 0 ]; do
    case "$1" in
        --home) [ $# -ge 2 ] || die "--home needs a path"; home="$2"; shift 2 ;;
        --uninstall) uninstall=1; shift ;;
        --no-init) no_init=1; shift ;;
        --force-hermes-link) force_link=1; shift ;;
        -h|--help) sed -n 2,5p "$0"; exit 0 ;;
        *) die "unknown option: $1" ;;
    esac
done

stop_service() {
    if [ "$(uname -s)" = Darwin ]; then
        launchctl bootout "gui/$(id -u)/io.skworld.sksidekick" 2>/dev/null || true
        rm -f "$plist"
    else
        systemctl --user disable --now sksidekick.service 2>/dev/null || true
        rm -f "$unit"
        systemctl --user daemon-reload 2>/dev/null || true
    fi
}

if [ -n "$uninstall" ]; then
    stop_service
    for link in "$HOME/.local/bin/sksidekick"; do
        if [ -L "$link" ] && case "$(readlink "$link")" in "$app"/*) true ;; *) false ;; esac; then rm -f "$link"; fi
    done
    rm -rf "$app"
    echo "Removed the sksidekick app and its service."
    echo "Your sidekick's data is still here: $home"
    echo "To delete it permanently: sksidekick --home \"$home\" delete --yes"
    exit 0
fi

# The Hermes installer links ~/.local/bin/hermes. Never take over a Hermes the person already runs.
existing="$HOME/.local/bin/hermes"
if [ -e "$existing" ] && [ -z "$force_link" ] && ! grep -qF "$app/" "$existing" 2>/dev/null; then
    die "$existing already exists (another Hermes on this machine). Use the container image instead, or re-run with --force-hermes-link."
fi

mkdir -p "$app/bin" "$app/logs"

say "uv $UV_VERSION"
if [ ! -x "$app/bin/uv" ]; then
    curl -LsSf "https://astral.sh/uv/$UV_VERSION/install.sh" | env UV_INSTALL_DIR="$app/bin" UV_NO_MODIFY_PATH=1 sh >/dev/null
fi
uv="$app/bin/uv"
"$uv" --version | grep -q "$UV_VERSION" || die "uv is not $UV_VERSION"

say "Hermes $HERMES_REF ($HERMES_COMMIT)"
inst="$app/hermes-install.sh"
curl -fsSL -o "$inst" "https://raw.githubusercontent.com/${HERMES_REPO#https://github.com/}/$HERMES_COMMIT/scripts/install.sh"
# its installer wants the release branch that holds the commit (a tag is refused); the commit pins the content
HERMES_REPO_URL="$HERMES_REPO.git" bash "$inst" --branch "$HERMES_BRANCH" --commit "$HERMES_COMMIT" \
    --dir "$app/hermes-agent" --hermes-home "$app/hermes-seed" \
    --non-interactive --skip-browser --skip-computer-use
[ "$(git -C "$app/hermes-agent" rev-parse HEAD)" = "$HERMES_COMMIT" ] || die "Hermes is not at $HERMES_COMMIT"
ln -sf "$app/hermes-agent/.hermes/bin/hermes" "$app/bin/hermes"

say "skmemory $SKMEMORY_VERSION (its own venv, outside Hermes')"
"$uv" venv -q --python 3.12 "$app/skmemory"
"$uv" pip install -q --python "$app/skmemory/bin/python" "skmemory==$SKMEMORY_VERSION"
ln -sf "$app/skmemory/bin/skmemory-mcp" "$app/bin/skmemory-mcp"

say "hermes-skmemory $HERMES_SKMEMORY_REF (bundled memory provider)"
tgz="$app/hermes-skmemory.tgz"
curl -fsSL -o "$tgz" "https://github.com/smilinTux/hermes-skmemory/archive/refs/tags/$HERMES_SKMEMORY_REF.tar.gz"
if command -v sha256sum >/dev/null; then got="$(sha256sum "$tgz")"; else got="$(shasum -a 256 "$tgz")"; fi
[ "${got%% *}" = "$HERMES_SKMEMORY_SHA256" ] || die "hermes-skmemory tarball checksum mismatch"
plugin="$app/hermes-agent/plugins/memory/skmemory"
rm -rf "$plugin" && mkdir -p "$plugin"
tar xzf "$tgz" -C "$plugin" --strip-components=1 --exclude='*/tests' --exclude='*/.github'
rm -f "$tgz"
cp "$here/skmemory.json" "$app/skmemory.json"

say "sksidekick"
"$uv" venv -q --python 3.12 "$app/sksidekick"
"$uv" pip install -q --python "$app/sksidekick/bin/python" "$repo"
ln -sf "$app/sksidekick/bin/sksidekick" "$app/bin/sksidekick"
install -m 0755 "$here/sidekick-start.sh" "$app/bin/sidekick-start"
mkdir -p "$HOME/.local/bin"
ln -sf "$app/bin/sksidekick" "$HOME/.local/bin/sksidekick"

if [ -n "$no_init" ]; then
    say "No sidekick created. Import yours: sksidekick --home \"$home\" import ARCHIVE --phrase-stdin"
elif [ ! -f "$home/state/instance.json" ]; then
    say "Creating your sidekick in $home"
    "$app/bin/sksidekick" --home "$home" init
fi

render() { sed -e "s|@APP@|$app|g" -e "s|@HOME@|$home|g" "$1"; }
say "Service"
if [ "$(uname -s)" = Darwin ]; then
    mkdir -p "$(dirname "$plist")"
    launchctl bootout "gui/$(id -u)/io.skworld.sksidekick" 2>/dev/null || true
    render "$here/io.skworld.sksidekick.plist" > "$plist"
    launchctl bootstrap "gui/$(id -u)" "$plist"
else
    mkdir -p "$(dirname "$unit")"
    render "$here/sksidekick.service" > "$unit"
    systemctl --user daemon-reload
    systemctl --user enable --now sksidekick.service
fi
echo "Done. Your sidekick's home: $home"
echo "Check it any time with: sksidekick --home \"$home\" status"
