#!/bin/sh
# XX-WM dev-loop deploy: rsync launcher/src/ to the phone's installed
# datadir (/usr/share/xx-wm/) and restart the running shell.
# Usage:
#   PIERCING_DEVICE=192.168.1.20 [XX_WM_USER=dr3k] scripts/deploy.sh [--dry-run]
# GitHub.com/PiercingXX

set -u

REPO_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)

DEVICE=${PIERCING_DEVICE:-}
USER_NAME=${XX_WM_USER:-dr3k}
DRY_RUN=0

for arg in "$@"; do
    case $arg in
        --dry-run) DRY_RUN=1 ;;
        -h|--help)
            echo "Usage: PIERCING_DEVICE=<host> [XX_WM_USER=dr3k] $0 [--dry-run]"
            exit 0
            ;;
        *)
            echo "Unknown argument: $arg" >&2
            exit 1
            ;;
    esac
done

if [ -z "$DEVICE" ]; then
    echo "PIERCING_DEVICE is required (hostname or IP of the phone)." >&2
    exit 1
fi

TARGET="${USER_NAME}@${DEVICE}"
DEST=/usr/share/xx-wm
SYNC=/usr/local/libexec/xx-wm-deploy-sync
RSYNC_FLAGS="-az --delete --exclude __pycache__"

# Restart the running shell: the systemd user unit only when it is the
# active shell (restarting an inactive unit under GDM would start a
# second shell). Otherwise SIGUSR1 the unique python child; the
# /usr/bin/xx-wm supervisor respawns it with fresh FDs. Gio.Application
# maps TERM/INT/HUP to quit (wait 0 → wrapper exits → phoc -E dies),
# so iterate never uses those.
# The child is matched on comm=python3 so the SSH controller (bash) cannot
# match. A pgrep '[p]ython3 /usr/share/xx-wm/main.py' pattern would also
# dodge self-match, but pgrep is not guaranteed on the device.
# shellcheck disable=SC2016  # expands remotely, not here
RESTART_CMD='
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=${XDG_RUNTIME_DIR}/bus}"
if command -v systemctl >/dev/null 2>&1 \
   && [ "$(ps -p 1 -o comm=)" = systemd ] \
   && systemctl --user is-active --quiet xx-wm; then
    systemctl --user restart xx-wm
else
    # Match on comm=python3 so the SSH controller (bash) cannot match.
    pid=$(ps -u "$(id -un)" -o pid=,comm=,args= | while read -r p c rest; do
        [ "$c" = python3 ] || continue
        case $rest in
            */usr/share/xx-wm/main.py*) echo "$p" ;;
        esac
    done)
    if [ -n "$pid" ] && [ "$(echo "$pid" | wc -l)" -eq 1 ]; then
        kill -USR1 "$pid"
    else
        echo "no unique python3 /usr/share/xx-wm/main.py to restart (got: $pid)" >&2
        exit 1
    fi
fi'

if [ "$DRY_RUN" = 1 ]; then
    echo "[dry-run] ssh $TARGET mktemp -d for a private staging dir"
    echo "[dry-run] rsync $RSYNC_FLAGS $REPO_DIR/launcher/src/ ${TARGET}:<staging>/"
    echo "[dry-run] rsync $RSYNC_FLAGS $REPO_DIR/../common/xx_ui/ ${TARGET}:<staging>/xx_ui/"
    echo "[dry-run] rsync -az $REPO_DIR/launcher/data/wayland.xml $REPO_DIR/launcher/data/wlr-foreign-toplevel-management-unstable-v1.xml $REPO_DIR/launcher/data/wlr-output-power-management-unstable-v1.xml $REPO_DIR/launcher/data/virtual-pointer-unstable-v1.xml $REPO_DIR/launcher/data/wlr-screencopy-unstable-v1.xml $REPO_DIR/launcher/data/phosh-private.xml ${TARGET}:<staging>/"
    echo "[dry-run] ssh $TARGET sudo rsync -a --chown=root:root <staging>/ ${DEST}/"
    echo "[dry-run] restart: systemctl --user restart xx-wm if active, else SIGUSR1 unique python3 /usr/share/xx-wm/main.py (wrapper respawns)"
    exit 0
fi

# A device set up by grant-tablet-sudo.sh lets the user run only the
# deploy sync helper without a password; other dev devices grant more.
echo "Checking passwordless sudo on ${TARGET}..."
# shellcheck disable=SC2029  # client-side expansion of SYNC is intended
if ssh "$TARGET" "sudo -n -l ${SYNC}" >/dev/null 2>&1; then
    USE_SYNC=1
elif ssh "$TARGET" 'sudo -n true'; then
    USE_SYNC=0
else
    echo "passwordless sudo is required to write ${DEST}. Run scripts/grant-tablet-sudo.sh" >&2
    exit 1
fi

# A fixed staging path is pre-creatable by another local user, whose files
# would then be copied into /usr/share/xx-wm as root. Stage in a private
# mktemp dir instead, and remove it on the way out.
STAGE=$(ssh "$TARGET" 'mktemp -d /tmp/xx-wm-deploy.XXXXXXXX') || exit 1
trap 'ssh "$TARGET" "rm -rf $STAGE"' EXIT INT TERM

echo "Deploying launcher/src/ -> ${TARGET}:${STAGE}/ then ${DEST}/"
# shellcheck disable=SC2086  # RSYNC_FLAGS is intentionally word-split
rsync $RSYNC_FLAGS "$REPO_DIR/launcher/src/" "${TARGET}:${STAGE}/" || exit 1
# The shared design system rides along into the datadir: the installed shell
# runs with the datadir on sys.path, so xx_ui resolves next to main.py even
# when common/ was never meson-installed on the device.
# shellcheck disable=SC2086
rsync $RSYNC_FLAGS "$REPO_DIR/../common/xx_ui/" "${TARGET}:${STAGE}/xx_ui/" || exit 1
# The protocol XMLs live next to the modules on the device; the shell
# regenerates its bindings when they change, so ship them with the code.
rsync -az "$REPO_DIR/launcher/data/wayland.xml" \
    "$REPO_DIR/launcher/data/wlr-foreign-toplevel-management-unstable-v1.xml" \
    "$REPO_DIR/launcher/data/wlr-output-power-management-unstable-v1.xml" \
    "$REPO_DIR/launcher/data/virtual-pointer-unstable-v1.xml" \
    "$REPO_DIR/launcher/data/wlr-screencopy-unstable-v1.xml" \
    "$REPO_DIR/launcher/data/phosh-private.xml" \
    "${TARGET}:${STAGE}/" || exit 1

# Datadir rsync is without --delete: --delete would wipe phoc.ini, sounds,
# squeekboard, gnome-osk. --chown keeps the meson-installed tree root-owned.
# shellcheck disable=SC2029  # client-side expansion of STAGE/DEST is intended
if [ "$USE_SYNC" = 1 ]; then
    ssh "$TARGET" "sudo -n ${SYNC} ${STAGE}" || exit 1
else
    ssh "$TARGET" "sudo rsync -a --chown=root:root ${STAGE}/ ${DEST}/" || exit 1
fi

echo "Restarting xx-wm on ${DEVICE}..."
# shellcheck disable=SC2029  # client-side expansion is intended
ssh "$TARGET" "$RESTART_CMD"
