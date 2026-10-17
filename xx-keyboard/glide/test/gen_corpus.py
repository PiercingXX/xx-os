#!/usr/bin/env python3
"""Synthetic glide corpus for the xx-keyboard gesture decoder test.

Reads the real layout JSON (us, us+colemak) and rebuilds the exact key
boxes pos_osk_widget_size_allocate() computes at a 360x200 allocation
(default key height 50): contiguous boxes, layer width 10 units, the
second-last row widened by shift/backspace (1.5u each), the last row by
the menu/123/enter keys with the space bar expanded to fill. Cross
checked pixel-for-pixel against a live PosOskWidget dump; see the
layout comment in test_glide_corpus.cpp.

For each word in words150.txt it swipes both layouts: a polyline
through the letter centres with corner cutting, occasional overshoot,
a constant finger offset plus per-sample jitter, resampled at 60-120Hz
with a human-like speed profile (slower into corners, rare hesitation
pauses), and a small loop on some doubled letters. All randomness is
seeded by (word, layout), so output is stable across runs.

Usage: gen_corpus.py [--check]
  Rewrites corpus.jsonl (old tester lines kept first, tagged
  layout=tester) and words.txt (parallel expected words) in this dir.
  --check only verifies the current files match what generation would
  produce, without writing.
"""
import hashlib
import json
import math
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LAYOUTS_DIR = os.path.normpath(os.path.join(HERE, "..", "..", "src", "layouts"))
CORPUS = os.path.join(HERE, "corpus.jsonl")
WORDS = os.path.join(HERE, "words.txt")
WORDLIST = os.path.join(HERE, "words150.txt")

WIDTH, HEIGHT, KEY_HEIGHT = 360, 200, 50
LAYOUTS = (("qwerty", "us.json"), ("colemak", "us+colemak.json"))


def widget_boxes(layout_path):
    """(sym, x, y, w, h) exactly as the widget allocates them."""
    rows = json.load(open(layout_path))["levels"][0]["rows"]
    n_rows = len(rows)
    widths = [sum(2.0 if k[0] == " " else 1.0 for k in r) for r in rows]
    for r in range(n_rows):
        if r == n_rows - 2:
            widths[r] += 3.0
        elif r == n_rows - 1:
            widths[r] += 4.0
    layer_w = max(widths)
    pitch = WIDTH / layer_w
    off_y = HEIGHT - n_rows * KEY_HEIGHT
    out = []
    for r, row in enumerate(rows):
        syms = [k[0] for k in row]
        units = [2.0 if s == " " else 1.0 for s in syms]
        pre, post = [], []
        if r == n_rows - 2:
            pre, post = [(None, 1.5)], [("KEY_BACKSPACE", 1.5)]
        elif r == n_rows - 1:
            pre, post = [(None, 1.0), (None, 1.0)], [("KEY_ENTER", 2.0)]
        row_w = sum(units) + sum(u for _, u in pre + post)
        if " " in syms:
            units[syms.index(" ")] += layer_w - row_w
            row_w = layer_w
        c = 0.5 * (layer_w - row_w)
        for sym, u in pre + list(zip(syms, units)) + post:
            out.append((sym, round(c * pitch), off_y + r * KEY_HEIGHT,
                        round(u * pitch), KEY_HEIGHT))
            c += u
    return out, pitch


def centres(boxes):
    return {s.lower(): (x + w / 2.0, y + h / 2.0)
            for s, x, y, w, h in boxes
            if s is not None and len(s) == 1 and s.isalpha()}


def seed_of(word, layout):
    return int(hashlib.sha1(("%s:%s" % (word, layout)).encode()).hexdigest()[:8], 16)


def synth_path(word, cen, pitch, rng):
    """Noisy finger path through the letter centres, in surface pixels.

    Sampling is time-based at 60-120 Hz; the speed (median ~550 px/s at
    360 px width, slower into corners) lands samples ~7 px apart, the
    same spatial density as the tester recordings. Much denser input
    makes every short word pay per-point skip costs and lose to long
    dictionary words, which is a sampling artefact, not accuracy.
    """
    verts = [cen[c] for c in word]
    pts = []
    n = len(verts)
    for i, v in enumerate(verts):
        if i == 0 or i == n - 1:
            pts.append(v)
            continue
        p, q = verts[i - 1], verts[i + 1]
        cut = rng.uniform(0.10, 0.30)
        a = (v[0] + (p[0] - v[0]) * cut, v[1] + (p[1] - v[1]) * cut)
        b = (v[0] + (q[0] - v[0]) * cut, v[1] + (q[1] - v[1]) * cut)
        if rng.random() < 0.25 and v != p:
            dx, dy = v[0] - p[0], v[1] - p[1]
            d = math.hypot(dx, dy) or 1.0
            o = rng.uniform(0.10, 0.30) * pitch
            a = (a[0] + dx / d * o, a[1] + dy / d * o)
        pts.append(a)
        if v == p and rng.random() < 0.5:
            r = 0.12 * pitch
            ang = rng.uniform(0, 2 * math.pi)
            for k in range(1, 4):
                t = ang + k * 2 * math.pi / 3
                pts.append((v[0] + r * math.cos(t), v[1] + r * math.sin(t)))
        pts.append(b)
    dx = max(-0.3 * pitch, min(0.3 * pitch, rng.gauss(0, 0.10 * pitch)))
    dy = max(-0.3 * pitch, min(0.3 * pitch, rng.gauss(0, 0.10 * pitch)))
    pts = [(x + dx, y + dy) for x, y in pts]
    # intermediate letter positions: the finger dwells near them
    zones = [verts[i] for i in range(1, n - 1)]
    zones = [(x + dx, y + dy) for x, y in zones]
    base = math.exp(rng.gauss(math.log(550.0), 0.30))
    segs, total = [], 0.0
    for i in range(len(pts) - 1):
        d = math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
        segs.append(d)
        total += d

    xs, ys, ts, t = [pts[0][0] + rng.gauss(0, 0.5)], [pts[0][1] + rng.gauss(0, 0.5)], [0], 0.0
    pause_at = rng.uniform(0.3, 0.7) * total if rng.random() < 0.15 else -1.0
    paused, dist = False, 0.0
    cur = list(pts[0])
    si, left = 0, segs[0] if segs else 0.0
    while si < len(segs):
        dt = rng.uniform(8.0, 16.0)
        near = min(math.hypot(cur[0] - z[0], cur[1] - z[1]) for z in zones) \
            if zones else pitch
        dwell = 0.65 if near < 0.30 * pitch else 1.0
        step = base * rng.uniform(0.9, 1.1) * dwell * dt / 1000.0
        dist += step
        if not paused and pause_at >= 0 and dist >= pause_at:
            for _ in range(rng.randint(3, 6)):
                t += rng.uniform(15.0, 25.0)
                xs.append(cur[0] + rng.gauss(0, 0.5))
                ys.append(cur[1] + rng.gauss(0, 0.5))
                ts.append(int(round(t)))
            paused = True
        t += dt
        while si < len(segs) and step >= left:
            step -= left
            si += 1
            left = segs[si] if si < len(segs) else 0.0
        if si >= len(segs):
            break
        left -= step
        frac = 1.0 - left / segs[si] if segs[si] > 0 else 1.0
        cur = [pts[si][0] + (pts[si + 1][0] - pts[si][0]) * frac,
               pts[si][1] + (pts[si + 1][1] - pts[si][1]) * frac]
        xs.append(cur[0] + rng.gauss(0, 0.5))
        ys.append(cur[1] + rng.gauss(0, 0.5))
        ts.append(int(round(t)))
    xs.append(pts[-1][0] + rng.gauss(0, 0.5))
    ys.append(pts[-1][1] + rng.gauss(0, 0.5))
    ts.append(int(round(t + rng.uniform(8.0, 16.0))))
    for i in range(1, len(ts)):
        ts[i] = max(ts[i], ts[i - 1] + 1)
    return [(int(round(x)), int(round(y)), t) for x, y, t in zip(xs, ys, ts)]


def key_entries(boxes):
    out = []
    for sym, x, y, w, h in boxes:
        if sym is None or sym in ("KEY_BACKSPACE",):
            continue
        if sym == "KEY_ENTER":
            code = 10
        elif sym == " ":
            code = 32
        elif len(sym) == 1 and sym.isalpha():
            code = ord(sym.lower())
        else:
            continue
        out.append([code, x, y, w, h])
    return out


def generate():
    words = [w.strip() for w in open(WORDLIST) if w.strip()]
    lines, expected = [], []
    for layout, fname in LAYOUTS:
        boxes, pitch = widget_boxes(os.path.join(LAYOUTS_DIR, fname))
        cen = centres(boxes)
        keys = key_entries(boxes)
        for word in words:
            rng = random.Random(seed_of(word, layout))
            path = synth_path(word, cen, pitch, rng)
            lines.append({"layout": layout, "word": word, "kw": WIDTH,
                          "kh": HEIGHT, "pitch": round(pitch), "keys": keys,
                          "path": [list(p) for p in path]})
            expected.append(word)
    return lines, expected


N_TESTER = 19


def same(a, b):
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def main():
    check = "--check" in sys.argv
    new_lines, new_words = generate()
    if check:
        have = [json.loads(l) for l in open(CORPUS) if l.strip()]
        have_words = [w.strip() for w in open(WORDS) if w.strip()]
        ok = (len(have) == N_TESTER + len(new_lines)
              and len(have_words) == len(have)
              and all(same(a, b) for a, b in zip(have[N_TESTER:], new_lines))
              and have_words[N_TESTER:] == new_words
              and all(l.get("layout") for l in have)
              and all(hw == hl.get("word", hw)
                      for hw, hl in zip(have_words, have) if hl.get("word")))
        print("corpus %s" % ("in sync" if ok else "STALE, regenerate"))
        return 0 if ok else 1
    old = [json.loads(l) for l in open(CORPUS) if l.strip()]
    old_words = [w.strip() for w in open(WORDS) if w.strip()]
    assert len(old) == len(old_words), "corpus/words out of sync"
    for line, word in zip(old, old_words):
        line.setdefault("layout", "tester")
        line.setdefault("word", word)
    if len(old) > N_TESTER or any(l.get("layout") != "tester" for l in old):
        print("refusing: corpus.jsonl already has generated lines; "
              "restore the %d tester lines first" % N_TESTER)
        return 1
    want = old + new_lines
    want_words = old_words + new_words
    with open(CORPUS, "w") as f:
        for line in want:
            f.write(json.dumps(line) + "\n")
    with open(WORDS, "w") as f:
        for w in want_words:
            f.write(w + "\n")
    print("wrote %d gestures (%d words x 2 layouts + %d tester) to %s" %
          (len(want), len(new_words) // 2, len(old), CORPUS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
