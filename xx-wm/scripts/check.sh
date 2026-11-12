#!/bin/sh
# XX-WM - Pre-commit gate script
# Runs py_compile + ruff + the main-loop block check + pyright (root helper and
# its client) + pytest + cargo test + shellcheck.
# A missing tool fails the gate. To skip one on purpose, name it in XX_CHECK_SKIP
# (e.g. XX_CHECK_SKIP="cargo shellcheck"); the skip is printed.

set -e

PYTHON="${PYTHON:-.venv/bin/python}"
if [ ! -x "$PYTHON" ]; then
    PYTHON=python3
fi
VENV_BIN=$(dirname "$PYTHON")

skipped() {
    case " ${XX_CHECK_SKIP:-} " in
        *" $1 "*) echo "$1 SKIPPED (XX_CHECK_SKIP)"; return 0 ;;
    esac
    return 1
}

missing() {
    echo "$1 FAILED - not found. $2"
    echo "(set XX_CHECK_SKIP=\"$1\" to skip it on purpose)"
    exit 1
}

PY_FILES=$(find launcher/src launcher/helper scripts tools -name '*.py' -not -path '*/reference/*')
PY_SCRIPTS="tools/xxdb"

echo "=== Checking Python syntax ==="
# shellcheck disable=SC2086
"$PYTHON" -m py_compile $PY_FILES $PY_SCRIPTS || {
    echo "Python syntax check FAILED"
    exit 1
}

echo "=== Running ruff ==="
if ! skipped ruff; then
    if "$PYTHON" -m ruff --version >/dev/null 2>&1; then
        RUFF="$PYTHON -m ruff"
    elif [ -x "$VENV_BIN/ruff" ]; then
        RUFF="$VENV_BIN/ruff"
    elif command -v ruff >/dev/null 2>&1; then
        RUFF=ruff
    else
        missing ruff "Install with: .venv/bin/pip install ruff"
    fi
    # shellcheck disable=SC2086
    $RUFF check launcher/src launcher/helper tests scripts tools $PY_SCRIPTS || {
        echo "ruff FAILED"
        exit 1
    }
fi

echo "=== Checking for main-loop blocking calls ==="
"$PYTHON" scripts/check_mainloop_blocks.py || {
    echo "main-loop block check FAILED"
    exit 1
}

echo "=== Running pyright (helper) ==="
if ! skipped pyright; then
    if [ -x "$VENV_BIN/pyright" ]; then
        PYRIGHT="$VENV_BIN/pyright"
    elif command -v pyright >/dev/null 2>&1; then
        PYRIGHT=pyright
    else
        missing pyright "Install with: .venv/bin/pip install pyright"
    fi
    "$PYRIGHT" --pythonpath "$(command -v "$PYTHON")" -p pyrightconfig.json || {
        echo "pyright FAILED"
        exit 1
    }
fi

echo "=== Running pytest ==="
if ! skipped pytest; then
    "$PYTHON" -m pytest --version >/dev/null 2>&1 \
        || missing pytest "Install with: .venv/bin/pip install pytest"
    "$PYTHON" -m pytest tests/ -q || {
        echo "pytest FAILED"
        exit 1
    }
fi

echo "=== Running cargo test (wm) ==="
if ! skipped cargo; then
    command -v cargo >/dev/null 2>&1 || missing cargo "Install Rust (e.g. pacman -S rust)"
    (cd wm && cargo test --quiet) || {
        echo "cargo test FAILED"
        exit 1
    }
fi

echo "=== Running shellcheck ==="
if ! skipped shellcheck; then
    if command -v shellcheck >/dev/null 2>&1; then
        SHELLCHECK=shellcheck
    elif [ -x "$VENV_BIN/shellcheck" ]; then
        SHELLCHECK="$VENV_BIN/shellcheck"
    else
        missing shellcheck "Install from the distro (pacman -S shellcheck) or .venv/bin/pip install shellcheck-py"
    fi
    for f in scripts/*.sh devices/*/*.sh tools/*.sh wm/dev/*.sh wm/river/*.sh \
             wm/river/session/* launcher/data/*.in; do
        [ -f "$f" ] || continue
        first=$(head -1 "$f")
        case "$first" in
            "#!"*sh*) ;;
            *) continue ;;
        esac
        dialect="sh"
        case "$first" in
            *bash*) dialect="bash" ;;
        esac
        "$SHELLCHECK" -s "$dialect" "$f" || {
            echo "shellcheck FAILED on $f"
            exit 1
        }
    done
fi

echo "=== All checks passed ==="
