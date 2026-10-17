#!/bin/sh
# Run every project's tests and lint (or only the projects named as arguments).
# Python: $PYTHON, else ./.venv, else xx-wm/.venv, else python3. xx-captions
# prefers its own .venv (sherpa-onnx/vosk); real-speech tests skip without it.
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
PROJECTS=${*:-"xx-wm common xx-contacts xx-dialer txxt xx-calendar xx-calculator xx-email xx-vitals xx-share xx-captions xx-find xx-auto xx-audiobook xx-radio xx-note xx-weather xx-files"}
pick_python() {
    for py in "${PYTHON:-}" "$ROOT/.venv/bin/python" "$ROOT/xx-wm/.venv/bin/python"; do
        [ -n "$py" ] && [ -x "$py" ] && { echo "$py"; return; }
    done
    echo python3
}
PY=$(pick_python)
failed=""
for p in $PROJECTS; do
    echo "=== $p"
    py=$PY
    [ "$p" = xx-captions ] && [ -x "$ROOT/xx-captions/.venv/bin/python" ] && py="$ROOT/xx-captions/.venv/bin/python"
    if [ "$p" = xx-wm ]; then
        (cd "$ROOT/xx-wm" && PATH="$(dirname "$py"):$PATH" sh scripts/check.sh) || failed="$failed $p"
    elif [ "$p" = common ]; then
        (cd "$ROOT/common" && "$py" -m pytest -q tests && "$py" -m ruff check xx_ui xx_phone tests tools) || failed="$failed $p"
    else
        (cd "$ROOT/$p" && "$py" -m pytest -q tests && "$py" -m ruff check src tests) || failed="$failed $p"
    fi
done
if [ -n "$failed" ]; then
    echo "FAILED:$failed"
    exit 1
fi
echo "ALL PASSED"
