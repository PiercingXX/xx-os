#!/usr/bin/env python3
"""Fail when blocking calls are reachable from main-loop code.

Flags ``subprocess.run`` / ``check_output`` / D-Bus ``call_sync`` /
``Popen(...).wait()`` / ``time.sleep`` / ``requests.*`` / libsecret
``*_sync`` anywhere in ``src/`` except inside the allow-listed worker
helpers below. Engine calls, resolves and probes run on
``xx_music.tasks.Worker`` or the player service pool and post back with
``GLib.idle_add``; GTK/D-Bus callbacks never block.

Each allow-list entry carries a one-line justification. Fix a violation by
moving the call to a worker thread, or add an entry with a justification.
Stale entries (matching no call site) also fail, so the list cannot
silently grow.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

SRC = Path(__file__).parent.parent / "src"

# (relative file, enclosing function) -> why blocking here is safe.
ALLOW = {
    ("xx_radio/youtube/probe.py", "_run_version"):
        "JS runtime version probe; callers are worker threads, never GTK.",
    ("xx_radio/youtube/resolver.py", "_run_payload"):
        "yt-dlp worker child; resolve() runs on stream-proxy/worker threads.",
    ("xx_radio/engine_status.py", "_default_runner"):
        "venv version probe / update steps; snapshot() and update() are "
        "worker-only, the screen uses snapshot_async().",
    ("xx_radio/setup.py", "_default_runner"):
        "uv/npm/git steps of engine setup; run from setup's own worker.",
}

_POPENISH = {"Popen", "_spawn", "_popen", "popen_factory"}


def _popen_names(tree: ast.AST) -> set[str]:
    """Names that may hold a Popen handle: assigned from a Popen-ish call."""
    names: set[str] = set()

    class Visitor(ast.NodeVisitor):
        def _call_target(self, node: ast.AST) -> str | None:
            if not isinstance(node, ast.Call):
                return None
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in _POPENISH:
                return func.attr
            if isinstance(func, ast.Name) and func.id in _POPENISH:
                return func.id
            return None

        def _record(self, target: ast.AST) -> None:
            if isinstance(target, ast.Name):
                names.add(target.id)
            elif isinstance(target, ast.Attribute):
                names.add("@" + target.attr)

        def visit_Assign(self, node: ast.Assign) -> None:
            if self._call_target(node.value) is not None:
                for target in node.targets:
                    self._record(target)
            self.generic_visit(node)

        def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
            if node.value is not None and self._call_target(node.value) \
                    is not None:
                self._record(node.target)
            self.generic_visit(node)

    Visitor().visit(tree)
    return names


def _iter_sites(path: Path) -> list[tuple[int, str, str, str]]:
    """(line, kind, enclosing function, source line) for each banned call."""
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    popen_names = _popen_names(tree)
    sites: list[tuple[int, str, str, str]] = []
    lines = text.splitlines()

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.stack: list[str] = []

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Call(self, node: ast.Call) -> None:
            func = node.func
            kind: str | None = None
            if isinstance(func, ast.Attribute):
                if func.attr in ("call_sync", "check_output"):
                    kind = func.attr
                elif func.attr == "run" and isinstance(func.value, ast.Name) \
                        and func.value.id == "subprocess":
                    kind = "subprocess.run"
                elif func.attr == "wait":
                    recv = func.value
                    if isinstance(recv, ast.Name) and (
                            recv.id == "proc" or recv.id in popen_names):
                        kind = "Popen.wait"
                    elif isinstance(recv, ast.Attribute) and (
                            "@" + recv.attr in popen_names):
                        kind = "Popen.wait"
                elif func.attr == "sleep" and isinstance(func.value, ast.Name) \
                        and func.value.id == "time":
                    kind = "time.sleep"
                elif func.attr in ("get", "post", "put", "delete", "head") \
                        and isinstance(func.value, ast.Name) \
                        and func.value.id == "requests":
                    kind = f"requests.{func.attr}"
                elif func.attr.endswith("_sync") and isinstance(
                        func.value, ast.Attribute) \
                        and func.value.attr == "Secret":
                    kind = f"Secret.{func.attr}"
            scope = self.stack[-1] if self.stack else "<module>"
            if kind is not None:
                shown = lines[node.lineno - 1].strip() if node.lineno else ""
                sites.append((node.lineno or 0, kind, scope, shown))
            self.generic_visit(node)

    Visitor().visit(tree)
    return sites


def main() -> int:
    failures: list[str] = []
    used: set[tuple[str, str]] = set()
    files = sorted(SRC.rglob("*.py")) if SRC.is_dir() else []
    for path in files:
        rel = str(path.relative_to(SRC))
        for lineno, kind, scope, shown in _iter_sites(path):
            key = (rel, scope)
            if key in ALLOW:
                used.add(key)
                continue
            failures.append(
                f"{rel}:{lineno}: {kind} in {scope}() "
                f"reachable from main-loop code: {shown}")
    stale = sorted(set(ALLOW) - used)
    if stale:
        failures.append(
            "stale allow-list entries (match no call site): "
            + ", ".join(f"{name}::{scope}" for name, scope in stale))
    if failures:
        print("main-loop blocking-call violations:")
        for failure in failures:
            print(f"  {failure}")
        print(f"\n{len(failures)} violation(s). Move the call to a worker "
              "thread or allow-list it with a one-line justification.")
        return 1
    print(f"main-loop block check OK "
          f"({len(used)}/{len(ALLOW)} allow-list entries exercised).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
