"""Check a loaded window against the database and the raw files it came from.

Usage:
    python3 analyst/verify_window.py 2026-08-23 2026-09-21 \
        --expect-reach https://www.instagram.com/reel/DdL7LXtslxX/=218543 \
        --expect-stage "High-ticket (Rebuilt, VIP/Apply)"
"""

import argparse
import json
from pathlib import Path

from db import run_sql

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("start")
    p.add_argument("end")
    p.add_argument("--expect-reach", action="append", default=[], metavar="PERMALINK=MIN")
    p.add_argument("--expect-stage")
    a = p.parse_args()
    window = f"""(posted_at at time zone 'Europe/London')::date between '{a.start}' and '{a.end}'"""
    failures = []

    # Row counts, by format, compared with the raw files for the same window.
    counts = {r["format"]: r["n"] for r in run_sql(
        f"select format, count(*) as n from content.content_posts where {window} group by format order by format")}
    raw_counts = {}
    for connector in ("reels", "posts"):
        meta_path = RAW_DIR / f"instagram_{connector}_{a.start}_{a.end}.meta.json"
        if meta_path.exists():
            raw_counts[connector] = json.loads(meta_path.read_text())["row_count"]
    db_total = sum(counts.values())
    raw_total = sum(raw_counts.values())
    print(f"content_posts in window: {db_total} {counts}")
    print(f"raw file rows:           {raw_total} {raw_counts}")
    if db_total != raw_total:
        failures.append(f"row count {db_total} in DB != {raw_total} in raw files")
    if "reels" in raw_counts and counts.get("reel", 0) != raw_counts["reels"]:
        failures.append(f"reels: {counts.get('reel', 0)} in DB != {raw_counts['reels']} in raw file")

    # Reach checks, from each post's latest snapshot.
    for spec in a.expect_reach:
        permalink, minimum = spec.rsplit("=", 1)
        rows = run_sql(f"""
            select s.reach, s.captured_at from content.content_posts p
            join content.content_metrics_snapshots s on s.post_id = p.id
            where p.permalink = '{permalink.replace("'", "''")}'
            order by s.captured_at desc limit 1""")
        if not rows:
            failures.append(f"no snapshot for {permalink}")
            print(f"reach {permalink}: MISSING")
            continue
        reach = rows[0]["reach"]
        print(f"reach {permalink}: {reach} (captured {rows[0]['captured_at']}, expected >= {minimum})")
        if reach is None or reach < int(minimum):
            failures.append(f"reach for {permalink} is {reach}, expected >= {minimum}")

    # Duplicates (whole table, not just the window).
    dupes = run_sql("""
        select 'permalink' as col, permalink as value, count(*) as n from content.content_posts group by permalink having count(*) > 1
        union all
        select 'shortcode', shortcode, count(*) from content.content_posts group by shortcode having count(*) > 1""")
    print(f"duplicate permalinks/shortcodes: {len(dupes)}")
    if dupes:
        failures.append(f"duplicates: {dupes}")

    # Snapshots: every post in the window has at least one.
    no_snap = run_sql(f"""
        select count(*) as n from content.content_posts p
        where {window} and not exists (select 1 from content.content_metrics_snapshots s where s.post_id = p.id)""")[0]["n"]
    print(f"posts in window without a snapshot: {no_snap}")
    if no_snap:
        failures.append(f"{no_snap} posts have no snapshot")

    # Stage assignment.
    stages = run_sql(f"""
        select coalesce(st.name, '<null>') as stage, count(*) as n
        from content.content_posts p left join content.account_stages st on st.id = p.stage_id
        where {window} group by 1 order by 1""")
    print(f"stage_id in window: {stages}")
    if any(r["stage"] == "<null>" for r in stages):
        failures.append("some posts have no stage_id")
    if a.expect_stage and any(r["stage"] != a.expect_stage for r in stages):
        failures.append(f"not every post is in stage {a.expect_stage!r}")

    print()
    if failures:
        print("FAIL")
        for f in failures:
            print(f"  - {f}")
        raise SystemExit(1)
    print("PASS")


if __name__ == "__main__":
    main()
