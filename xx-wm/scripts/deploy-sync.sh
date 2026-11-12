#!/bin/sh
# Root half of scripts/deploy.sh on a test device. grant-tablet-sudo.sh
# installs this as /usr/local/libexec/xx-wm-deploy-sync and lets the
# test user run only this command without a password. It copies one
# private staging directory that the calling user owns into the shell's
# datadir and nothing else:
#   sudo -n /usr/local/libexec/xx-wm-deploy-sync /tmp/xx-wm-deploy.XXXXXXXX
# GitHub.com/PiercingXX

set -eu
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
DEST=/usr/share/xx-wm

refuse() {
    echo "xx-wm-deploy-sync: refusing: $*" >&2
    exit 2
}

[ "$#" -eq 1 ] || refuse "usage: xx-wm-deploy-sync /tmp/xx-wm-deploy.XXXXXXXX"
src=$1
case $src in
    /tmp/xx-wm-deploy.*) ;;
    *) refuse "$src is not a deploy staging directory" ;;
esac
case ${src#/tmp/xx-wm-deploy.} in
    ''|*[!A-Za-z0-9]*) refuse "$src is not a deploy staging directory" ;;
esac
caller=${SUDO_UID:-}
case $caller in
    ''|*[!0-9]*|0) refuse "run through sudo as the deploying user" ;;
esac
[ -d "$src" ] && [ ! -L "$src" ] || refuse "$src is not a directory"
# Work from inside the directory so swapping the path for a symlink
# after the checks cannot redirect the copy.
cd -P -- "$src" || refuse "cannot enter $src"
[ "$(stat -c %u .)" = "$caller" ] || refuse "$src is not owned by the caller"
[ "$(stat -c %a .)" = 700 ] || refuse "$src is not private (mode 700)"

# No owner, group or setuid bits from the stage: everything lands
# root-owned, not group/world writable; links leaving the tree are dropped.
exec rsync -rlpt --safe-links --chown=root:root --chmod=ug-s,go-w \
    -- ./ "$DEST/"
