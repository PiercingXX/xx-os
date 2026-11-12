#!/bin/sh
# Run the XX-WM shell from this checkout inside the current Wayland display
# (the nested river from run-nested.sh), sealed off from the host desktop:
# its own HOME and its own D-Bus session, so it cannot take the host's
# notification daemon or settings. App standby and wireless debugging are
# off. Start it through the window manager so it lands on river:
#   xxwm msg action spawn sh /path/to/run-shell.sh
# The on-screen keyboard runs in the same sealed session, so the shell's
# SetVisible reaches it: an installed xx-keyboard, or a build tree's binary
# named by XX_KEYBOARD (its schemas are read from the build's data/).
set -eu
here=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
home=${XX_WM_DEV_HOME:-${XDG_RUNTIME_DIR:-/tmp}/xx-wm-dev-home}
log=${XX_WM_DEV_LOG:-$home/shell-stderr.log}

if [ ! -f "$home/.config/xx-wm/config.json" ]; then
    mkdir -p "$home/.config/xx-wm" "$home/.local/share" "$home/.cache"
    cat > "$home/.config/xx-wm/config.json" <<'EOF'
{"theme": "amoled", "setup_complete": true, "default_layout_applied": true,
 "app_standby_minutes": 0, "xxdb_enabled": false, "usage_stats_enabled": false,
 "home_slots": [
  {"type": "app", "label": "Terminal", "app_id": "kitty.desktop", "cmd": null, "folder": null},
  {"type": "app", "label": "Files", "app_id": "org.gnome.Nautilus.desktop", "cmd": null, "folder": null},
  {"type": "app", "label": "Calculator", "app_id": "org.gnome.Calculator.desktop", "cmd": null, "folder": null}
 ]}
EOF
fi

kb=${XX_KEYBOARD:-$(command -v xx-keyboard || true)}
kb_schemas=
if [ -n "$kb" ] && [ -f "$(dirname -- "$kb")/../data/gschemas.compiled" ]; then
    kb_schemas=$(CDPATH='' cd -- "$(dirname -- "$kb")/../data" && pwd)
fi

cd "$here/launcher"
# shellcheck disable=SC2016 # expanded by the inner shell
exec env HOME="$home" \
    XDG_CONFIG_HOME="$home/.config" \
    XDG_DATA_HOME="$home/.local/share" \
    XDG_CACHE_HOME="$home/.cache" \
    PYTHONPATH="$here/launcher/src:$here/../common" \
    XX_KB="$kb" XX_KB_SCHEMAS="$kb_schemas" \
    dbus-run-session -- sh -c '
        if [ -n "$XX_KB" ]; then
            (
                [ -n "$XX_KB_SCHEMAS" ] && export GSETTINGS_SCHEMA_DIR="$XX_KB_SCHEMAS"
                gsettings set org.gnome.desktop.a11y.applications screen-keyboard-enabled true
                exec "$XX_KB"
            ) &
        fi
        exec env LD_PRELOAD=/usr/lib/libgtk4-layer-shell.so.0 python3 src/main.py
    ' >"$log" 2>&1
