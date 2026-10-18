#!/usr/bin/env python3
"""Fill a HOME/XDG dir with realistic xx-vitals data via the real store APIs.

Usage: python3 xx-vitals/tools/demo_seed.py OUT_HOME [--days N]

Writes prefs, 30 days of daily metrics, a week of walks/runs with
Android-style millisecond timestamps, weight samples, queued writes and a
sync cursor, mirroring the Android screenshots (6,900 steps / 90 pts today,
goal cards, journal threads). Idempotent: re-running replaces the same rows.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
LANE_ROOT = os.path.dirname(os.path.dirname(TOOLS_DIR))
sys.path.insert(0, os.path.join(LANE_ROOT, "common"))
sys.path.insert(0, os.path.join(LANE_ROOT, "xx-vitals", "src"))

SOURCE = "phone"


def _local_ms(day: datetime, hour: int, minute: int) -> int:
    return int(day.replace(hour=hour, minute=minute, second=0, microsecond=0).timestamp() * 1000)


def main(argv: list[str]) -> int:
    out_home = argv[1] if len(argv) > 1 else None
    if not out_home:
        print("usage: demo_seed.py OUT_HOME [--days N]", file=sys.stderr)
        return 2
    days = 30
    if "--days" in argv:
        days = int(argv[argv.index("--days") + 1])

    from xx_vitals import prefs as prefs_module
    from xx_vitals.paths import ensure_app_dirs
    from xx_vitals.store import entries
    from xx_vitals.store.db import Activity, DailyMetric, Database, SyncState

    ensure_app_dirs()
    now = datetime.now().astimezone()
    today = now.date()

    cfg = prefs_module.AppConfig(
        steps_goal=10000,
        effort_goal=90,
        weekly_effort_goal=150,
        height_metres=1.78,
        birth_year=1990,
        units="imperial",
        collect_enabled=False,
    )
    prefs_module.save(cfg)

    db = Database()
    db.init()

    # Last `days` days: today mirrors the Android shot (6,900 steps,
    # 90 effort pts, 1,722 Cal, 9.0 km, 90 active min). Three of the last
    # seven hit the 90-pt goal; the ISO week sums to 90 of 150.
    week_steps = [6900, 9000, 1200, 2900, 15000, 4500, 8100]
    week_effort = [90, 101, 12, 13, 180, 40, 95]
    week_cal = [1722, 1900, 320, 505, 2400, 900, 1750]
    week_km = [9.0, 8.03, 1.1, 1.96, 12.4, 3.8, 7.3]
    week_min = [90, 101, 15, 30, 150, 55, 95]
    older_steps = [3200, 0, 7800, 5400, 11000, 2600, 0, 6900, 8300, 4100,
                   9800, 1500, 6700, 0, 7200, 5900, 8800, 3300, 10400, 2100,
                   7600, 4900, 9300]
    older_effort = [35, 0, 80, 55, 120, 25, 0, 70, 85, 42,
                    105, 15, 68, 0, 74, 60, 92, 30, 110, 20,
                    78, 50, 96]
    for back in range(days):
        day = today - timedelta(days=back)
        if back < 7:
            steps, effort = week_steps[back], week_effort[back]
            cal, km, active = week_cal[back], week_km[back], week_min[back]
        else:
            slot = (back - 7) % len(older_steps)
            steps, effort = older_steps[slot], older_effort[slot]
            cal = round(steps * 0.25) if steps else None
            km = round(steps * 0.0013, 2) if steps else None
            active = min(180, steps // 100) if steps else None
        db.upsert_daily(DailyMetric(
            source_id=SOURCE, date=day.isoformat(),
            steps=steps or None,
            distance_m=round(km * 1000, 1) if km else None,
            active_min=active, effort_points=effort or None,
            calories_kcal=float(cal) if cal else None,
            hr_avg=74 if steps else None, hr_min=62 if steps else None,
            hr_max=121 if steps else None, derived_mask=0,
        ))

    # Journal threads with Android-style millisecond ids.
    journal = [
        (0, "walk", 8, 33, 90, 9.0, 320.0, 90, 90, 6900, "Morning walk"),
        (1, "walk", 1, 34, 101, 8.03, 505.0, 101, 88, 9000, "Late night walk"),
        (2, "walk", 17, 44, 30, 1.96, 120.0, 13, 30, 2900, "Evening walk"),
        (3, "run", 18, 19, 90, 9.0, 640.0, 178, 90, 15000, "Evening run"),
        (4, "walk", 12, 5, 45, 3.8, 210.0, 40, 45, 4500, "Lunch walk"),
        (6, "cycle", 9, 15, 60, 14.2, 380.0, 60, 60, None, "Morning ride"),
    ]
    for back, kind, hour, minute, span_min, km, cal, pts, active, steps, _name in journal:
        day = now - timedelta(days=back)
        start = _local_ms(day, hour, minute)
        end = start + span_min * 60_000
        db.insert_activity(Activity(
            source_id=SOURCE, type=kind, started_at=start, ended_at=end,
            distance_m=km * 1000, calories_kcal=cal, effort_points=pts,
            active_min=active, hr_avg=106 if kind == "walk" else 132,
            hr_max=121 if kind == "walk" else 158, steps=steps,
        ))

    for back, weight in ((0, 80.0), (1, 79.5), (3, 79.8)):
        at = _local_ms(now - timedelta(days=back), 7, 30)
        db.upsert_weight(entries.WeightSample(
            source_id="manual", measured_at=at, weight_kg=weight))

    at_ms = int(now.timestamp() * 1000)
    db.pending_insert(entries.PendingWrite(
        kind="weight",
        payload_json=entries.weight_payload("manual", at_ms, 80.0),
        created_at=at_ms))
    db.pending_insert(entries.PendingWrite(
        kind="daily",
        payload_json=entries.daily_payload(DailyMetric(
            source_id=SOURCE, date=today.isoformat(), steps=6900,
            derived_mask=0)),
        created_at=at_ms))
    db.sync_upsert(SyncState(
        id=1, last_pull_at=at_ms, last_push_at=at_ms - 1000,
        last_pull_iso=now.isoformat()))
    print(f"seeded {db.count_dailies()} days, {db.count_activities()} "
          f"activities, {db.count_weights()} weights in {out_home}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
