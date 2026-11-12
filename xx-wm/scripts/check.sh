#!/bin/sh
# XX-WM - Pre-commit gate script
# Runs py_compile on all sources + ruff + pytest + shellcheck

set -e

echo "=== Checking Python syntax ==="
PYTHON="${PYTHON:-.venv/bin/python}"
if [ ! -x "$PYTHON" ]; then
    PYTHON=python3
fi
find launcher/src -name '*.py' -print0 | xargs -0 "$PYTHON" -m py_compile || {
    echo "Python syntax check FAILED"
    exit 1
}

echo "=== Running ruff ==="
ruff_check() {
    "$@" check launcher/src tests || {
        echo "ruff FAILED"
        exit 1
    }
}
if "$PYTHON" -m ruff --version >/dev/null 2>&1; then
    ruff_check "$PYTHON" -m ruff
elif [ -x .venv/bin/ruff ]; then
    ruff_check .venv/bin/ruff
elif command -v ruff >/dev/null 2>&1; then
    ruff_check ruff
else
    echo "ruff SKIPPED - not found (install with: .venv/bin/pip install ruff)"
fi

echo "=== Checking for main-loop blocking calls ==="
"$PYTHON" scripts/check_mainloop_blocks.py || {
    echo "main-loop block check FAILED"
    exit 1
}

echo "=== Running pytest ==="
if command -v pytest >/dev/null 2>&1 || [ -x .venv/bin/pytest ]; then
    "$PYTHON" -m pytest tests/ -q || {
        echo "pytest FAILED"
        exit 1
    }
else
    echo "pytest not available, skipping"
fi

echo "=== Running cargo test (wm) ==="
if command -v cargo >/dev/null 2>&1; then
    (cd wm && cargo test --quiet) || {
        echo "cargo test FAILED"
        exit 1
    }
else
    echo "cargo test SKIPPED - cargo not found"
fi

echo "=== Running shellcheck ==="
if command -v shellcheck >/dev/null 2>&1; then
    for f in scripts/*.sh devices/*/*.sh; do
        if [ -f "$f" ]; then
            # -s sh, or -s bash where the shebang says bash
            dialect="sh"
            case "$(head -1 "$f")" in
                *bash*) dialect="bash" ;;
            esac
            shellcheck -s "$dialect" "$f" || {
                echo "shellcheck FAILED on $f"
                exit 1
            }
        fi
    done
else
    echo "shellcheck SKIPPED - not found (install from distro, e.g. pacman -S shellcheck)"
fi

echo "=== All checks passed ==="
