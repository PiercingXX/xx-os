#!/bin/sh
# Run river nested in the current Wayland session with xxwm as its window
# manager, for development. Two outputs by default (WLR_WL_OUTPUTS) so the
# handset and desktop policies can be watched side by side: make one window
# portrait and it becomes the handset.
#   XXWM_LOG=debug   verbose window-manager log
#   XXWM_BIN=...     window manager binary (default: the debug build)
set -eu
here=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
bin=${XXWM_BIN:-$here/target/debug/xxwm}
log=${XXWM_LOG_FILE:-${XDG_RUNTIME_DIR:-/tmp}/xxwm-nested.log}
[ -x "$bin" ] || { echo "build first: cargo build (in $here)" >&2; exit 1; }
export WLR_WL_OUTPUTS="${WLR_WL_OUTPUTS:-2}"
echo "river nested, log: $log" >&2
exec river -no-xwayland -c "XXWM_LOG='${XXWM_LOG:-}' exec '$bin'" >"$log" 2>&1
