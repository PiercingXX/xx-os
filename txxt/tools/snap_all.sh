#!/bin/sh
# Render every TxxT screen in four themes and montage one labeled contact
# sheet per theme into OUTDIR, plus fit checks: every screen at 320 px
# wide and at large text (text-size scale 1.5) on 360 px, montaged into
# sheet-fit320.png and sheet-scale15.png. Exits non-zero on any snap
# WARNING (a window wider or taller than its phone screen) or any failed
# render.
#   txxt/tools/snap_all.sh OUTDIR
# Env: THEMES, FIT320, SCALE15 (screen lists; empty skips that check).
set -u

OUTDIR=${1:?usage: snap_all.sh OUTDIR}
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
LANE_ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
TX="$LANE_ROOT/txxt"
PY=${PYTHON:-python3}
export PYTHONPATH="$TX/src$([ -n "${PYTHONPATH:-}" ] && printf ':%s' "$PYTHONPATH")"

SCREENS="list thread mms search new settings blocking quarantine rowmenu
overflow attach staged"
THEMES=${THEMES-"amoled paper forest aura"}
FIT320=${FIT320-$SCREENS}
SCALE15=${SCALE15-$SCREENS}
mkdir -p "$OUTDIR"
failed=""

write_navs() {
    # $1=dir: one navigation script per screen ("list" needs none)
    cat > "$1/thread.py" <<'EOF'
import json, os
_ids = json.load(open(os.path.join(os.environ['SEED_HOME'], 'ids.json')))
win.open_thread(_ids['mara'])
EOF
    cat > "$1/mms.py" <<'EOF'
import json, os
_ids = json.load(open(os.path.join(os.environ['SEED_HOME'], 'ids.json')))
win.open_thread(_ids['juniper'])
EOF
    cat > "$1/search.py" <<'EOF'
import json, os
_ids = json.load(open(os.path.join(os.environ['SEED_HOME'], 'ids.json')))
win.open_thread(_ids['mara'])
page = win._thread_pages[-1]
page._search_bar.set_search_mode(True)
page._search_entry.set_text('the')
EOF
    cat > "$1/new.py" <<'EOF'
win.open_new_conversation()
EOF
    cat > "$1/settings.py" <<'EOF'
win.open_settings()
EOF
    cat > "$1/blocking.py" <<'EOF'
win.open_blocking()
EOF
    cat > "$1/quarantine.py" <<'EOF'
win.open_quarantine()
EOF
    cat > "$1/rowmenu.py" <<'EOF'
import json, os
_ids = json.load(open(os.path.join(os.environ['SEED_HOME'], 'ids.json')))
win._list_page._show_row_menu(_ids['mara'])
EOF
    cat > "$1/overflow.py" <<'EOF'
win._list_page._on_overflow_clicked()
EOF
    cat > "$1/attach.py" <<'EOF'
import json, os
_ids = json.load(open(os.path.join(os.environ['SEED_HOME'], 'ids.json')))
win.open_thread(_ids['mara'])
win._thread_pages[-1]._on_attach_clicked()
EOF
    cat > "$1/staged.py" <<'EOF'
import glob, json, os
_ids = json.load(open(os.path.join(os.environ['SEED_HOME'], 'ids.json')))
win.open_thread(_ids['juniper'])
src = sorted(glob.glob(os.path.join(
    os.environ['SEED_HOME'], '.local', 'share', 'txxt', 'media', '*')))[0]
win._thread_pages[-1]._stage_photo(src)
EOF
}

render_set() {
    # $1=sheet name $2=theme $3=text-size scale $4=size $5=screens
    sheet=$1 theme=$2 scale=$3 size=$4 screens=$5
    SEED_HOME=$(mktemp -d "${TMPDIR:-/tmp}/txxt-seed-XXXXXX")
    NAV=$(mktemp -d "${TMPDIR:-/tmp}/txxt-nav-XXXXXX")
    export SEED_HOME
    "$PY" "$TX/tools/demo_seed.py" "$SEED_HOME" >/dev/null || {
        echo "seed failed for $sheet" >&2; failed="$failed $sheet-seed"
        rm -rf "$SEED_HOME" "$NAV"; return; }
    mkdir -p "$SEED_HOME/.config/xx-wm"
    printf '{"theme": "%s", "text_size_scale": %s}' "$theme" "$scale" \
        > "$SEED_HOME/.config/xx-wm/config.json"
    export HOME="$SEED_HOME" \
        XDG_DATA_HOME="$SEED_HOME/.local/share" \
        XDG_CONFIG_HOME="$SEED_HOME/.config" \
        XDG_CACHE_HOME="$SEED_HOME/.cache"
    write_navs "$NAV"

    shots=""
    for name in $screens; do
        out="$NAV/shot-$name.png"
        script=""
        [ "$name" = list ] || script="$NAV/$name.py"
        # The headless rig is racy starting broadwayd back-to-back, so a
        # hard failure (no PNG at all) retries; a WARNING means the
        # content truly overflowed and fails the gate immediately.
        attempt=0
        while [ $attempt -lt 3 ]; do
            attempt=$((attempt + 1))
            rm -f "$out"
            if log=$("$PY" "$LANE_ROOT/scripts/snap.py" --out "$out" \
                    --size "$size" --delay 2.0 --path "$TX/src" \
                    --path "$LANE_ROOT/common" \
                    ${script:+--script "$script"} -- -m txxt 2>&1) \
                    && [ -f "$out" ]; then
                break
            fi
            echo "snap $name ($sheet) attempt $attempt failed; retrying" >&2
            sleep 2
        done
        if [ ! -f "$out" ]; then
            echo "snap $name ($sheet): no PNG after 3 tries" >&2
            failed="$failed $sheet/$name"
            continue
        fi
        case "$log" in
            *"snap: WARNING"*)
                echo "snap $name ($sheet): $log" >&2
                failed="$failed $sheet/$name"
                cp "$out" "$OUTDIR/failed-$sheet-$name.png" ;;
        esac
        shots="$shots -label $name $out"
    done

    # shellcheck disable=SC2086
    montage $shots -tile 6x -geometry 360x720+4+4 -background black \
        -fill white "$OUTDIR/sheet-$sheet.png" || failed="$failed $sheet-montage"
    rm -rf "$SEED_HOME" "$NAV"
}

for theme in $THEMES; do
    render_set "$theme" "$theme" 1 360x720 "$SCREENS"
done
[ -n "$FIT320" ] && render_set fit320 amoled 1 320x720 "$FIT320"
[ -n "$SCALE15" ] && render_set scale15 amoled 1.5 360x720 "$SCALE15"

if [ -n "$failed" ]; then
    echo "FAILED:$failed" >&2
    exit 1
fi
echo "sheets in $OUTDIR"
