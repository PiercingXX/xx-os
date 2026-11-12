#!/bin/sh
# Let scripts/deploy.sh write the shell's datadir on the XX-WM smoke
# tablet without a password, from this laptop.
#
# Run ON THIS COMPUTER (not on the tablet). You will be prompted once for
# dr3k's *tablet* sudo password. It installs scripts/deploy-sync.sh as
# /usr/local/libexec/xx-wm-deploy-sync (root-owned) and a sudoers drop-in
# that lets the user run only that command without a password; it copies
# one private staging dir the user owns into /usr/share/xx-wm and nothing
# else. Every other sudo still asks for the password. Running it again
# replaces an older drop-in that granted NOPASSWD: ALL.
#
#   sh scripts/grant-tablet-sudo.sh
#   sudo sh scripts/grant-tablet-sudo.sh
#
# Local sudo is optional: if you use it, the script re-execs as $SUDO_USER
# so your SSH keys still apply. Defaults match the smoke tablet:
#   PIERCING_DEVICE=192.168.1.129  XX_WM_USER=dr3k
#
# Revoke later:
#   sh scripts/grant-tablet-sudo.sh --revoke
# GitHub.com/PiercingXX

set -u

# Local root would SSH as root and miss this user's keys. Drop back.
if [ "$(id -u)" -eq 0 ]; then
    if [ -z "${SUDO_USER:-}" ] || [ "$SUDO_USER" = root ]; then
        echo "Do not run as root. Use: sh $0" >&2
        echo "Or: sudo -u <your-user> $0" >&2
        exit 1
    fi
    exec sudo -u "$SUDO_USER" -- "$0" "$@"
fi

DEVICE=${PIERCING_DEVICE:-192.168.1.129}
USER_NAME=${XX_WM_USER:-dr3k}
REVOKE=0
DRY_RUN=0
DROPIN=xx-wm-smoke
SYNC=/usr/local/libexec/xx-wm-deploy-sync
SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)

for arg in "$@"; do
    case $arg in
        --revoke) REVOKE=1 ;;
        --dry-run) DRY_RUN=1 ;;
        -h|--help)
            echo "Usage: [PIERCING_DEVICE=host] [XX_WM_USER=user] $0 [--revoke] [--dry-run]"
            echo "Installs ${SYNC} and /etc/sudoers.d/${DROPIN} on the tablet:"
            echo "XX_WM_USER may run only ${SYNC} without a password."
            exit 0
            ;;
        *)
            echo "Unknown argument: $arg" >&2
            exit 1
            ;;
    esac
done

# Interpolated into a remote sh -c string — keep it a unix username.
case $USER_NAME in
    ''|*[!A-Za-z0-9._-]*)
        echo "Refusing XX_WM_USER='$USER_NAME' (must be a unix username)." >&2
        exit 1
        ;;
esac

TARGET="${USER_NAME}@${DEVICE}"

if [ "$REVOKE" = 1 ]; then
    echo "Remove /etc/sudoers.d/${DROPIN} and ${SYNC} on ${TARGET}."
    echo "Type ${USER_NAME}'s sudo password on the tablet if prompted."
    if [ "$DRY_RUN" = 1 ]; then
        echo "[dry-run] ssh -t $TARGET sudo rm -f /etc/sudoers.d/${DROPIN} ${SYNC}"
        exit 0
    fi
    ssh -t "$TARGET" "sudo rm -f /etc/sudoers.d/${DROPIN} ${SYNC} && sudo visudo -c"
    echo "Revoked. ${USER_NAME} needs a password for sudo again (except reboot/shutdown)."
    exit 0
fi

RULE="${USER_NAME} ALL=(root) NOPASSWD: ${SYNC}"
echo "Install ${SYNC} and /etc/sudoers.d/${DROPIN} on ${TARGET}:"
echo "  ${RULE}"
echo "Type ${USER_NAME}'s sudo password on the tablet if prompted."

if [ "$DRY_RUN" = 1 ]; then
    echo "[dry-run] scp ${SCRIPT_DIR}/deploy-sync.sh ${TARGET}:<private dir>/"
    echo "[dry-run] ssh -t $TARGET sudo sh -c '<install ${SYNC} + write drop-in + visudo -c>'"
    exit 0
fi

# Copied into a private (0700) dir first; root installs it from there.
REMOTE_TMP=$(ssh "$TARGET" 'mktemp -d') || exit 1
scp -q "${SCRIPT_DIR}/deploy-sync.sh" "${TARGET}:${REMOTE_TMP}/deploy-sync.sh" || exit 1

# File contents live in the remote command (not stdin) so `ssh -t` can use
# the tty for the sudo password. sudoers.d filenames must not contain '.' .
# visudo -c (no -f) checks the whole sudoers tree after install.
ssh -t "$TARGET" "sudo sh -c '
set -eu
dest=/etc/sudoers.d/${DROPIN}
install -d -o root -g root -m 0755 /usr/local/libexec
install -o root -g root -m 0755 ${REMOTE_TMP}/deploy-sync.sh ${SYNC}
tmp=\$(mktemp)
trap \"rm -f \$tmp\" EXIT
printf \"%s\\n\" \
  \"# XX-WM smoke tablet: scripts/deploy.sh may sync /usr/share/xx-wm without a password.\" \
  \"${RULE}\" > \"\$tmp\"
chmod 0440 \"\$tmp\"
visudo -c -f \"\$tmp\"
install -o root -g root -m 0440 \"\$tmp\" \"\$dest\"
visudo -c
printf \"installed %s and %s\\n\" \"${SYNC}\" \"\$dest\"
'"
_status=$?
# shellcheck disable=SC2029  # client-side expansion is intended
ssh "$TARGET" "rm -rf ${REMOTE_TMP}"
[ "$_status" = 0 ] || exit 1

echo "Checking passwordless deploy sync..."
if ssh -o BatchMode=yes -o ConnectTimeout=8 "$TARGET" "sudo -n -l ${SYNC}" >/dev/null; then
    echo "OK: ${TARGET} can run ${SYNC} without a password."
else
    echo "Drop-in written but sudo -n -l ${SYNC} still failed. Check /etc/sudoers.d/${DROPIN} on the tablet." >&2
    exit 1
fi
