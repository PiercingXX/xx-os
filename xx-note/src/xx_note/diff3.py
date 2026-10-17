"""Line-based three-way merge (port of ``core/Diff3.kt``).

Classic diff3 from the published algorithm: O(nm) LCS alignment of
base<->local and base<->remote, split on lines aligned on BOTH sides,
resolve each chunk (local==base take remote; remote==base take local;
local==remote take once; else conflict). Refusal is first-class.
"""

from __future__ import annotations

from dataclasses import dataclass

CONTEXT_LINES = 3


@dataclass(frozen=True)
class Hunk:
    base_lines: tuple[str, ...]
    local_lines: tuple[str, ...]
    remote_lines: tuple[str, ...]
    context_before: tuple[str, ...]
    context_after: tuple[str, ...]


def _align(base: list[str], other: list[str]) -> list[int]:
    n, m = len(base), len(other)
    suf = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            if base[i] == other[j]:
                suf[i][j] = suf[i + 1][j + 1] + 1
            else:
                suf[i][j] = max(suf[i + 1][j], suf[i][j + 1])
    into = [-1] * n
    i = j = 0
    while i < n and j < m:
        if base[i] == other[j]:
            into[i] = j
            i += 1
            j += 1
        elif suf[i + 1][j] >= suf[i][j + 1]:
            i += 1
        else:
            j += 1
    return into


def merge(
    base: list[str], local: list[str], remote: list[str]
) -> tuple[str, object]:
    """Returns ``("merged", lines)`` or ``("conflicted", [Hunk, ...])``."""
    pieces = _plan(base, local, remote)
    stable = [p[1] for p in pieces if p[0] == "stable"]
    out: list[str] = []
    hunks: list[Hunk] = []
    for piece in pieces:
        if piece[0] == "stable":
            out.append(piece[1])
            continue
        _, b, loc, rem, pos = piece
        resolved = _resolve(b, loc, rem)
        if resolved is not None:
            out.extend(resolved)
        else:
            hunks.append(
                Hunk(
                    tuple(b), tuple(loc), tuple(rem),
                    tuple(stable[max(0, pos - CONTEXT_LINES): pos]),
                    tuple(stable[pos: pos + CONTEXT_LINES]),
                )
            )
    if not hunks:
        return ("merged", out)
    return ("conflicted", hunks)


def merge_with_markers(
    base: list[str],
    local: list[str],
    remote: list[str],
    ours_label: str = "ours",
    theirs_label: str = "theirs",
) -> list[str]:
    pieces = _plan(base, local, remote)
    out: list[str] = []
    for piece in pieces:
        if piece[0] == "stable":
            out.append(piece[1])
            continue
        _, b, loc, rem, _pos = piece
        resolved = _resolve(b, loc, rem)
        if resolved is not None:
            out.extend(resolved)
        else:
            out.append(f"<<<<<<< {ours_label}")
            out.extend(loc)
            out.append("=======")
            out.extend(rem)
            out.append(f">>>>>>> {theirs_label}")
    return out


def _plan(
    base: list[str], local: list[str], remote: list[str]
) -> list[tuple]:
    lm = _align(base, local)
    rm = _align(base, remote)
    pieces: list[tuple] = []
    stable: list[str] = []

    def region(b_from: int, b_to: int, l_from: int, l_to: int,
               r_from: int, r_to: int) -> None:
        bseg = base[b_from:b_to]
        lseg = local[l_from:l_to]
        rseg = remote[r_from:r_to]
        if not bseg and not lseg and not rseg:
            return
        pieces.append(("region", bseg, lseg, rseg, len(stable)))

    pb = pl = pr = -1
    for i in range(len(base)):
        li, ri = lm[i], rm[i]
        if li >= 0 and ri >= 0:
            region(pb + 1, i, pl + 1, li, pr + 1, ri)
            pieces.append(("stable", base[i]))
            stable.append(base[i])
            pb, pl, pr = i, li, ri
    region(pb + 1, len(base), pl + 1, len(local), pr + 1, len(remote))
    return pieces


def _resolve(
    base: list[str], local: list[str], remote: list[str]
) -> list[str] | None:
    if local == base:
        return list(remote)
    if remote == base:
        return list(local)
    if local == remote:
        return list(local)
    return None
