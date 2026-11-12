#!/bin/sh
# Cut a signed xx-os release: an SSH-signed annotated tag on the checked-out
# commit, which must be clean, on the remote's main, a descendant of the
# previous release, and green in CI. The tag is verified against the
# release-signers file the shell ships before it is pushed. Phones install
# only from such tags (docs/versioning.md, "Releases").
#
# Usage: scripts/release.sh [--force] [--remote NAME] [--key PATH] [TAG]
#   TAG       vYYYY.MM.DD or vYYYY.MM.DD.N; default today's UTC date, with
#             the next free .N when today already has a release
#   --key     private signing key (default $XX_RELEASE_KEY, else
#             $HOME/.ssh/xx-os-release)
#   --remote  remote to fetch main from and push the tag to (default origin)
#   --force   go ahead when CI cannot be checked (gh missing, logged out or
#             failing); never overrides a CI run gh reports as not passed
set -eu

die() {
    echo "release: $*" >&2
    exit 1
}

warn() {
    echo "release: warning: $*" >&2
}

FORCE=0
REMOTE=origin
KEY=${XX_RELEASE_KEY:-$HOME/.ssh/xx-os-release}
GH=${XX_RELEASE_GH:-gh}
TAG=
PRINCIPAL=release@xx-os
TAG_RE='v[0-9]{4}\.[0-9]{2}\.[0-9]{2}(\.[0-9]+)?'

while [ $# -gt 0 ]; do
    case $1 in
        --force) FORCE=1 ;;
        --remote)
            [ $# -ge 2 ] || die "--remote needs a name"
            REMOTE=$2
            shift ;;
        --key)
            [ $# -ge 2 ] || die "--key needs a path"
            KEY=$2
            shift ;;
        -h|--help)
            sed -n '2,15p' "$0"
            exit 0 ;;
        -*) die "unknown option: $1" ;;
        *)
            [ -z "$TAG" ] || die "give at most one tag"
            TAG=$1 ;;
    esac
    shift
done

ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || die "not inside a git checkout"
cd "$ROOT"
SIGNERS=$ROOT/xx-wm/launcher/data/release-signers
[ -f "$SIGNERS" ] || die "missing $SIGNERS"
[ -f "$KEY" ] || die "signing key not found: $KEY"

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    die "the working tree has uncommitted changes; commit or stash them first"
fi
SHA=$(git rev-parse HEAD)

# Phones rebuild only apps whose version went up: a changed app without a
# bump would ship nothing.
if [ -f "$ROOT/scripts/app_versions.py" ]; then
    python3 "$ROOT/scripts/app_versions.py" check --root "$ROOT" >&2 ||
        die "per-app versions are not bumped (scripts/app_versions.py bump <app>); phones would not install those changes"
fi

git fetch --quiet --tags "$REMOTE" || die "could not fetch tags from $REMOTE"
git fetch --quiet "$REMOTE" main || die "could not fetch $REMOTE main"
git merge-base --is-ancestor "$SHA" FETCH_HEAD ||
    die "HEAD ($SHA) is not on $REMOTE main; push it first"

is_release() {
    printf '%s\n' "$1" | grep -Eqx "$TAG_RE"
}

PREVIOUS=$(git tag --list 'v*' | grep -Ex "$TAG_RE" | sort -V | tail -n 1 || true)

if [ -z "$TAG" ]; then
    base=v$(date -u +%Y.%m.%d)
    TAG=$base
    n=1
    while git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; do
        TAG=$base.$n
        n=$((n + 1))
    done
else
    is_release "$TAG" || die "tag $TAG does not match vYYYY.MM.DD[.N]"
    git rev-parse -q --verify "refs/tags/$TAG" >/dev/null &&
        die "tag $TAG already exists"
fi

if [ -n "$PREVIOUS" ]; then
    newest=$(printf '%s\n%s\n' "$PREVIOUS" "$TAG" | sort -V | tail -n 1)
    [ "$newest" = "$TAG" ] ||
        die "tag $TAG must be newer than the latest release $PREVIOUS (phones install the newest tag)"
    prev_sha=$(git rev-parse "refs/tags/$PREVIOUS^{commit}")
    [ "$prev_sha" != "$SHA" ] || die "$SHA is already released as $PREVIOUS"
    git merge-base --is-ancestor "$prev_sha" "$SHA" ||
        die "HEAD does not descend from $PREVIOUS; phones would refuse it"
fi

# 0 passed, 1 not passed (reason on stdout), 2 could not ask.
ci_state() {
    command -v "$GH" >/dev/null 2>&1 || return 2
    runs=$("$GH" run list --commit "$SHA" \
        --json status,conclusion,workflowName \
        --jq '.[] | "\(.status) \(.conclusion) \(.workflowName)"' \
        2>/dev/null) || return 2
    if [ -z "$runs" ]; then
        echo "CI has no run for $SHA yet"
        return 1
    fi
    bad=$(printf '%s\n' "$runs" | awk 'NF && ($1 != "completed" || $2 != "success")')
    if [ -n "$bad" ]; then
        printf 'CI has not passed for %s:\n%s\n' "$SHA" "$bad"
        return 1
    fi
    return 0
}

ci_rc=0
ci_detail=$(ci_state) || ci_rc=$?
case $ci_rc in
    0) echo "CI passed for $SHA." ;;
    1) die "$ci_detail" ;;
    *)
        if [ "$FORCE" = 1 ]; then
            warn "could not check CI with $GH; going ahead because of --force"
        else
            die "could not check CI with $GH (install and log in to gh, or check CI for $SHA yourself and pass --force)"
        fi ;;
esac

git -c gpg.format=ssh -c gpg.ssh.program=ssh-keygen \
    -c user.signingkey="$KEY" \
    tag -s -m "xx-os $TAG" "$TAG" "$SHA" || die "signing $TAG failed"

if ! out=$(LC_ALL=C git -c gpg.format=ssh -c gpg.ssh.program=ssh-keygen \
        -c gpg.ssh.allowedSignersFile="$SIGNERS" -c gpg.minTrustLevel=fully \
        verify-tag "$TAG" 2>&1) ||
    ! printf '%s\n' "$out" | grep -qF "Good \"git\" signature for $PRINCIPAL with"; then
    git tag -d "$TAG" >/dev/null
    die "the new tag does not verify against $SIGNERS (wrong key?): $out"
fi

git push --quiet "$REMOTE" "refs/tags/$TAG" ||
    die "pushing $TAG failed; the signed tag stays local"
echo "Released $TAG ($SHA)."
