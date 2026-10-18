#!/bin/sh
# Render the in-shell Settings (menu, every category subpage at each
# viewport, and the sheets/menus its rows open) for every theme.
#   xx-wm/tools/snap_settings.sh OUTDIR [theme ...]
# Same seeded temp HOME and zero-`snap: WARNING` gate as snap_shell.sh,
# at 360x720 (SNAP_SIZE=450x1000 renders another phone, e.g. the FLX1
# at scale 1.6). One shell launch per theme drives every shot
# (snap_settings_nav.py, SNAP_MULTI); themes render in parallel, each on
# its own session bus (the shell is a single-instance app). Output:
# sheet-<theme>-settings-<n>.png contact sheets (KEEP_SHOTS=1 also keeps
# the single shots). SNAP_SETTINGS_ONLY="network privacy" limits pages;
# SNAP_TEXT_SCALE=1.5 seeds large text.
set -u
REPO=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
OUTDIR=${1:?usage: snap_settings.sh OUTDIR [theme ...]}
SIZE=${SNAP_SIZE:-360x720}
TEXT_SCALE=${SNAP_TEXT_SCALE:-1.0}
shift || true
if [ "$#" -gt 0 ]; then
    THEMES="$*"
else
    THEMES="amoled paper aura"
fi
mkdir -p "$OUTDIR"
OUTDIR=$(CDPATH='' cd -- "$OUTDIR" && pwd)
WORK=$(mktemp -d)
if [ "${KEEP_WORK:-0}" = 1 ]; then
    trap 'echo "work kept: $WORK"' EXIT INT TERM
else
    trap 'rm -rf "$WORK"' EXIT INT TERM
fi

APPS="Phone:phone Messages:messages Contacts:contacts Calendar:calendar Camera:camera Maps:maps Notes:notes Clocks:clocks Files:files Settings:settings Weather:weather Vitals:vitals Email:email Share:share Calculator:calculator Photos:photos Music:music Browser:browser Terminal:terminal Software:software"

seed_home() {
    _home=$1 _theme=$2
    mkdir -p "$_home/.config/xx-wm" "$_home/.local/share/applications" "$_home/.cache"
    for pair in $APPS; do
        _name=${pair%%:*} _id=${pair##*:}
        printf '[Desktop Entry]\nName=%s\nExec=true\nType=Application\n' "$_name" \
            > "$_home/.local/share/applications/${_id}.desktop"
    done
    cat > "$_home/.config/xx-wm/config.json" <<EOF
{"theme": "$_theme", "text_size_scale": $TEXT_SCALE,
 "pin_hash": "8d969eef6ecad3c29a3a629280e686cf0c3f5d5a86aff3ca12020c923adc6c92",
 "apn": "internet",
 "medical_id": {"name": "Alex Doe", "blood_type": "O+",
                "allergies": "Penicillin",
                "emergency_contacts": [{"name": "Sam Doe", "number": "+1 555 0100"}]},
 "standby_exempt": ["calculator.desktop"],
 "smart_lock_wifi_ssids": ["HomeWifi"],
 "home_slots": [
  {"type": "app", "label": "Notes", "app_id": "notes.desktop", "cmd": null, "folder": null},
  {"type": "app", "label": "Calendar", "app_id": "calendar.desktop", "cmd": null, "folder": null},
  {"type": "app", "label": "Weather", "app_id": "weather.desktop", "cmd": null, "folder": null}
]}
EOF
}

run_theme() {
    _theme=$1
    _home="$WORK/home-$_theme"
    _shots="$WORK/shots-$_theme"
    _err="$WORK/$_theme.err"
    mkdir -p "$_shots"
    seed_home "$_home" "$_theme"
    # Parallel themes can race for a Broadway display (Gtk then fails to
    # initialize and nothing is drawn): retry a run that drew no menu.
    _try=0
    while [ "$_try" -lt 3 ]; do
        _try=$((_try + 1))
        HOME="$_home" XDG_CONFIG_HOME="$_home/.config" \
            XDG_DATA_HOME="$_home/.local/share" \
            XDG_CACHE_HOME="$_home/.cache" \
            XDG_DATA_DIRS="$_home/.local/share" \
            SNAP_MULTI=1 SNAP_SETTINGS_OUT="$_shots" SNAP_SETTINGS_THEME="$_theme" \
            SNAP_SETTINGS_ONLY="${SNAP_SETTINGS_ONLY:-}" \
            dbus-run-session -- python3 "$REPO/scripts/snap.py" --out "$_shots/done.png" --delay 2.0 --size "$SIZE" \
            --path "$REPO/common" --path "$REPO/xx-wm/launcher/src" \
            --script "$REPO/xx-wm/tools/snap_settings_nav.py" \
            -- "$REPO/xx-wm/launcher/src/main.py" >"$WORK/$_theme.out" 2>"$_err"
        [ -f "$_shots/$_theme-menu.png" ] && break
        sleep 2
    done
}

for theme in $THEMES; do
    run_theme "$theme" &
done
wait

status=0
for theme in $THEMES; do
    echo "=== $theme"
    _shots="$WORK/shots-$theme"
    _err="$WORK/$theme.err"
    if [ ! -f "$_shots/$theme-menu.png" ]; then
        echo "snap failed: $theme"
        tail -n 20 "$_err"
        status=1
        continue
    fi
    if grep -q 'snap: WARNING\|snap: no row' "$_err"; then
        grep 'snap: WARNING\|snap: no row' "$_err"
        status=1
    fi
    rm -f "$_shots/done.png" "$_shots/$theme-end.png"
    # Shots in capture order, 4x2 per contact sheet.
    _list=$(ls -tr "$_shots"/*.png)
    _n=0 _page=0 _batch=""
    for shot in $_list; do
        _label=$(basename "$shot" .png)
        _label=${_label#"$theme"-}
        _batch="$_batch -label $_label $shot"
        _n=$((_n + 1))
        if [ "$_n" = 8 ]; then
            # shellcheck disable=SC2086
            montage $_batch -tile 4x2 -geometry "$SIZE+4+4" -background black \
                -fill white -pointsize 20 \
                "$OUTDIR/sheet-$theme-settings-$_page.png" || status=1
            _n=0 _page=$((_page + 1)) _batch=""
        fi
    done
    if [ -n "$_batch" ]; then
        # shellcheck disable=SC2086
        montage $_batch -tile 4x2 -geometry "$SIZE+4+4" -background black \
            -fill white -pointsize 20 \
            "$OUTDIR/sheet-$theme-settings-$_page.png" || status=1
    fi
    echo "wrote $OUTDIR/sheet-$theme-settings-*.png"
    if [ "${KEEP_SHOTS:-0}" = 1 ]; then
        cp "$_shots"/*.png "$OUTDIR/"
    fi
done
exit "$status"
