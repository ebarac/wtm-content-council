"""Fetch reels and posts for a date range, split up front so no response hits the size cap.

The Metricool connector caps responses at about 25k tokens (spec section 2,
note 2). Before fetching, this pulls Metricool's own daily reel and post counts
(evolution connector: IGEV22, IGEV04) for the range and estimates each
connector's response size. Any window estimated over MAX_BYTES is halved, and
halved again if still too big. Reels and posts use the same windows, so each
window can be loaded and verified on its own.

Each window is then fetched with fetch_metricool.fetch(), which keeps its own
3-attempt retry for ordinary failures.

The last line of output lists the windows, for the caller to load and verify:
    WINDOWS 2025-03-01:2025-03-15 2025-03-16:2025-03-31

Usage:
    python3 analyst/fetch_month.py 2025-03-01 2025-03-31
"""

import argparse
import json
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone

import fetch_metricool as fm

# Feb 2025: 40 reels -> 38,228 bytes (~950 bytes/row) came back fine;
# Mar 2025: ~46 reels (~44 KB) failed. Stay well under the ~38 KB that worked.
BYTES_PER_ROW = 950
MAX_BYTES = 32_000
COUNT_FIELDS = ["IGEV22", "IGEV04"]  # reels published, posts published


def daily_counts(start: date, end: date, model: str) -> tuple[Counter, Counter]:
    """Per-day reel and post counts from Metricool's evolution connector. Raw response is saved."""
    lo, hi = fm.london_bounds(start, end)
    expected = {"brandId": fm.BRAND_ID, "from": lo, "to": hi, "metrics": COUNT_FIELDS}
    last_error = None
    for attempt in range(1, fm.ATTEMPTS + 1):
        try:
            text = fm.extract_result(fm.run_headless(expected, model), expected)
            rows = json.loads(text)["rows"]
            break
        except Exception as exc:  # noqa: BLE001 - same retry policy as fetch_metricool
            last_error = exc
            print(f"counts attempt {attempt}/{fm.ATTEMPTS} failed: {exc}", file=sys.stderr)
            if attempt < fm.ATTEMPTS:
                time.sleep(10 * 2 ** (attempt - 1))
    else:
        raise SystemExit(f"FAILED evolution counts {start}..{end} after {fm.ATTEMPTS} attempts: {last_error}")

    fm.RAW_DIR.mkdir(parents=True, exist_ok=True)
    (fm.RAW_DIR / f"instagram_evolution_{start}_{end}.json").write_text(text, encoding="utf-8")
    (fm.RAW_DIR / f"instagram_evolution_{start}_{end}.meta.json").write_text(json.dumps({
        "connector": "evolution", "brand_id": fm.BRAND_ID, "from": lo, "to": hi,
        "fields": COUNT_FIELDS + ["<date yyyymmdd>"],
        "fetched_at": datetime.now(timezone.utc).isoformat(), "row_count": len(rows),
    }, indent=2) + "\n", encoding="utf-8")

    reels, posts = Counter(), Counter()
    for r in rows:
        day = datetime.strptime(str(r[-1]), "%Y%m%d").date()
        reels[day] += int(float(r[0] or 0))
        posts[day] += int(float(r[1] or 0))
    return reels, posts


def plan(start: date, end: date, reels: Counter, posts: Counter) -> list[tuple[date, date]]:
    """Halve the window until each connector's estimated response is under MAX_BYTES."""
    days = [start + timedelta(n) for n in range((end - start).days + 1)]
    biggest = max(sum(reels[d] for d in days), sum(posts[d] for d in days))
    if biggest * BYTES_PER_ROW <= MAX_BYTES or start == end:
        return [(start, end)]
    mid = start + timedelta(len(days) // 2 - 1)  # e.g. March -> 1-15 and 16-31
    return plan(start, mid, reels, posts) + plan(mid + timedelta(1), end, reels, posts)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("start", type=date.fromisoformat)
    p.add_argument("end", type=date.fromisoformat)
    p.add_argument("--model", default="haiku")
    a = p.parse_args()
    if a.end < a.start:
        p.error("end is before start")

    reels, posts = daily_counts(a.start, a.end, a.model)
    windows = plan(a.start, a.end, reels, posts)
    print(f"Metricool counts {a.start}..{a.end}: {sum(reels.values())} reels, {sum(posts.values())} posts "
          f"-> {len(windows)} window(s)")
    for s, e in windows:
        fm.fetch("reels", s, e, a.model)
        fm.fetch("posts", s, e, a.model)
    print("WINDOWS " + " ".join(f"{s}:{e}" for s, e in windows))


if __name__ == "__main__":
    main()
