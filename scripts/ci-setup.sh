#!/bin/sh
# Install everything scripts/check-all.sh needs on a fresh Arch Linux, as root.
# CI runs this in an archlinux container; run it in podman to reproduce CI:
#   podman run --rm -v "$PWD":/src:ro archlinux:latest sh /src/scripts/ci-setup.sh --run
set -eu
pacman -Syu --noconfirm --needed \
    git rsync sudo which base-devel \
    python python-pip python-gobject python-cairo python-pytest shellcheck \
    gtk4 libadwaita gtk4-layer-shell gobject-introspection libnotify \
    gstreamer gst-plugins-base gst-plugins-good \
    dbus nftables util-linux xorg-server-xvfb xorg-xauth tzdata \
    rust \
    python-pyopenssl python-pillow python-cryptography python-dateutil \
    python-fonttools python-libevdev python-pynacl python-numpy \
    python-phonenumbers python-pywayland python-requests python-secretstorage \
    python-zeroconf yt-dlp zbar
pip install --break-system-packages --root-user-action=ignore \
    ruff ytmusicapi pyzbar skia-pathops
id ci >/dev/null 2>&1 || useradd -m ci
git config --system --add safe.directory '*'
if [ "${1:-}" = --run ]; then
    src=$(cd "$(dirname "$0")/.." && pwd)
    rsync -a --exclude .venv --exclude target --exclude build "$src"/ /home/ci/xx-os/
    chown -R ci:ci /home/ci/xx-os
    exec sudo -u ci env HOME=/home/ci sh -c 'cd /home/ci/xx-os && xvfb-run -a sh scripts/check-all.sh'
fi
