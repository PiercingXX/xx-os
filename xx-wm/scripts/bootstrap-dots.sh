#!/bin/sh
# XX-WM — owner's update-system.sh installer.
# Runs ON THE PHONE. Fetches PiercingXX's update-system.sh from the
# piercing-dots repo and installs it where piercing-dots' sync_scripts
# puts it (~/.scripts/PiercingXX-Settings-Menu/update-system.sh, plus
# ~/.scripts/lib/common.sh which the script sources). The script
# self-updates from GitHub on every run afterwards, so this installer
# only needs to run once. Opt out with XX_WM_NO_UPDATE_SCRIPT=1.
#
# Nothing here applies an upstream profile that does not exist: the
# installer only copies the update script and its library.

set -eu

REPO="https://github.com/PiercingXX/piercing-dots"
SCRIPT_REL="resources/.scripts/PiercingXX-Settings-Menu/update-system.sh"
LIB_REL="resources/.scripts/lib/common.sh"

if [ "${XX_WM_NO_UPDATE_SCRIPT:-0}" = 1 ]; then
    echo "XX_WM_NO_UPDATE_SCRIPT=1: leaving ~/.scripts alone."
    exit 0
fi

if command -v git >/dev/null 2>&1; then :; else
    echo "git is required (apk add git / sudo apt install git)" >&2
    exit 1
fi

if [ -d "${HOME}/piercing-dots" ]; then
    DEST="${HOME}/piercing-dots"
    # Keep the user's clone; never delete this path.
else
    DEST="${HOME}/.cache/piercing-dots"
    rm -rf "$DEST"
    git clone --depth 1 "$REPO" "$DEST"
fi

install_one() {
    # install_one <repo-relative source> <home-relative target> <mode>:
    # copy one file out of the clone, leaving symlinks (dev checkouts)
    # and up-to-date files alone.
    _src="$DEST/$1"
    _dest="${HOME}/$2"
    _mode=$3
    if [ ! -f "$_src" ]; then
        echo "warn: $1 not found in $DEST" >&2
        return 1
    fi
    if [ -L "$_dest" ]; then
        echo "$_dest is a symlink (dev checkout); not overwriting it."
        return 0
    fi
    if [ -f "$_dest" ] && cmp -s "$_src" "$_dest"; then
        return 0
    fi
    mkdir -p "$(dirname "$_dest")"
    cp -f "$_src" "$_dest"
    chmod "$_mode" "$_dest"
    echo "Installed $_dest"
}

install_one "$SCRIPT_REL" ".scripts/PiercingXX-Settings-Menu/update-system.sh" 755
install_one "$LIB_REL" ".scripts/lib/common.sh" 644
