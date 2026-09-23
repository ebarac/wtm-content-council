"""Select the step 4 hand-tagging sample: 40 posts, stratified, reproducible.

1. Excludes posts with no caption, and posts already used as spec examples or
   test cases during the build (EXCLUDE below).
2. Splits 40 across stages in proportion to each stage's share of eligible
   posts, with at least MIN_PER_STAGE from every stage.
3. Within each stage, splits that stage's quota across cells of
   (format, is_partnership, cta_keyword set) in proportion to the cell sizes.
4. Makes sure the sample includes every format and at least one partnership.
5. Picks at random within each cell (fixed SEED), at most one post per
   duplicate group.

Writes docs/tagging_sample.csv sorted by posted_at, numbered 1-40, with the stage name.

Usage:
    python3 analyst/select_tagging_sample.py
"""

import csv
import random
from collections import Counter, defaultdict
from pathlib import Path

from db import run_sql

SAMPLE_SIZE = 40
MIN_PER_STAGE = 6
SEED = 20260923
OUT = Path(__file__).resolve().parent.parent / "docs" / "tagging_sample.csv"

EXCLUDE = {
    # Section 11 tag test cases
    "DdL7LXtslxX": "tag test case: 12 Sep 2026 vagus nerve reel",
    "Dcwp7_fM4KG": "tag test case: 1 Sep 2026 Ozempic reel",
    "DdXZQNkss0l": "tag test case: 16 Sep 2026 numbered symptoms list",
    # Section 6 partnership examples
    "DdmDa4EMVEf": "partnership example: 22 Sep 2026 Humann",
    "DdJj_JOMLc1": "partnership example: 11 Sep 2026 MitoQ",
    "DcjLk1msrMh": "partnership example: 27 Aug 2026 Ritual",
    # Section 6 duplicate examples
    "Dc6yoxgMPh4": "duplicate example: 5 Sep 2026 pair",
    "Dc6ymlQMzEX": "duplicate example: 5 Sep 2026 pair",
    "Dcmc3X1MHp2": "duplicate example: 28 Aug 2026 triple",
    "Dcmce6bs7aX": "duplicate example: 28 Aug 2026 triple",
    "DcmcQ11MkWH": "duplicate example: 28 Aug 2026 triple",
    # Other posts used as test cases or discussed during the build
    "DGEYo79s5Sg": "flag_posts test case: reel half of the reel/carousel pair",
    "DGOg-oetwDF": "flag_posts test case: carousel half of the reel/carousel pair",
    "C8NlpM4sUkM": "spec section 8: June 2024 peak reel",
    "DIMYSzyMemT": "partnership override, discussed in step 3",
    "DaMBxLhMesa": "multi-keyword caption, discussed in step 3",
    "DaMBXhiMSMp": "multi-keyword caption, discussed in step 3",
    "DalmuS1sfrA": "multi-keyword caption, discussed in step 3",
    "DalmeFhscuH": "multi-keyword caption, discussed in step 3",
    "DbTB0W6MVEY": "multi-keyword caption, discussed in step 3",
}


def largest_remainder(weights: dict, total: int, minimum: dict | None = None) -> dict:
    """Split total across keys in proportion to weights, honouring per-key minimums."""
    minimum = minimum or {}
    alloc = {k: minimum.get(k, 0) for k in weights}
    rest = total - sum(alloc.values())
    free = {k: w for k, w in weights.items()}
    # Proportional share of the full total; keys already at/above it keep their minimum.
    share = {k: total * w / sum(weights.values()) for k, w in free.items()}
    extra = {k: max(0.0, share[k] - alloc[k]) for k in free}
    scale = rest / sum(extra.values()) if sum(extra.values()) else 0
    exact = {k: extra[k] * scale for k in free}
    floors = {k: int(v) for k, v in exact.items()}
    for k in floors:
        alloc[k] += floors[k]
    left = rest - sum(floors.values())
    for k in sorted(exact, key=lambda k: (-(exact[k] - floors[k]), str(k)))[:left]:
        alloc[k] += 1
    return alloc


def main() -> None:
    posts = run_sql("""
        select p.shortcode, p.permalink, p.format, p.caption, p.is_partnership, p.cta_keyword,
               p.stage_id, p.duplicate_group_id, p.posted_at,
               to_char(p.posted_at at time zone 'Europe/London', 'YYYY-MM-DD HH24:MI') as posted_london
        from content.content_posts p""")
    total = len(posts)
    no_caption = [p for p in posts if not (p["caption"] or "").strip()]
    excluded = [p for p in posts if p["shortcode"] in EXCLUDE]
    missing = set(EXCLUDE) - {p["shortcode"] for p in posts}
    if missing:
        raise SystemExit(f"exclusion list names posts not in the table: {sorted(missing)}")
    eligible = [p for p in posts if (p["caption"] or "").strip() and p["shortcode"] not in EXCLUDE]
    print(f"posts {total}; no caption {len(no_caption)}; excluded examples {len(excluded)}; eligible {len(eligible)}")

    def cell(p):
        return (p["format"], bool(p["is_partnership"]), p["cta_keyword"] is not None)

    by_stage = defaultdict(list)
    for p in eligible:
        by_stage[p["stage_id"]].append(p)
    stage_quota = largest_remainder({s: len(v) for s, v in by_stage.items()}, SAMPLE_SIZE,
                                    {s: MIN_PER_STAGE for s in by_stage})

    cell_quota = {}
    for s, members in by_stage.items():
        sizes = Counter(cell(p) for p in members)
        for c, n in largest_remainder(sizes, stage_quota[s]).items():
            cell_quota[(s, c)] = n

    # Every format, and at least one partnership, must appear. If proportional
    # allocation left one out, move a slot to it from the biggest cell in the
    # stage where that category is most common.
    def ensure(test, label):
        if any(n for (s, c), n in cell_quota.items() if test(c)):
            return
        options = Counter()
        for p in eligible:
            if test(cell(p)):
                options[(p["stage_id"], cell(p))] += 1
        (s, c), _ = options.most_common(1)[0]
        donor = max((k for k in cell_quota if k[0] == s and cell_quota[k] > 0 and not test(k[1])),
                    key=lambda k: (cell_quota[k], str(k)))
        cell_quota[donor] -= 1
        cell_quota[(s, c)] = cell_quota.get((s, c), 0) + 1
        print(f"  moved one slot in stage {s} from {donor[1]} to {c} so the sample includes {label}")

    for fmt in sorted({p["format"] for p in eligible}):
        ensure(lambda c, f=fmt: c[0] == f, f"a {fmt}")
    ensure(lambda c: c[1], "a partnership")

    rng = random.Random(SEED)
    chosen, used_groups = [], set()
    for (s, c) in sorted(cell_quota, key=str):
        n = cell_quota[(s, c)]
        pool = sorted((p for p in by_stage[s] if cell(p) == c), key=lambda p: p["shortcode"])
        rng.shuffle(pool)
        picked = 0
        for p in pool:
            if picked == n:
                break
            if p["duplicate_group_id"] and p["duplicate_group_id"] in used_groups:
                continue
            chosen.append(p)
            used_groups.add(p["duplicate_group_id"]) if p["duplicate_group_id"] else None
            picked += 1
        if picked < n:
            raise SystemExit(f"stage {s} cell {c}: needed {n}, only {picked} available")

    chosen.sort(key=lambda p: p["posted_at"])
    stage_names = {r["id"]: r["name"] for r in run_sql("select id, name from content.account_stages")}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["row", "permalink", "posted_at", "stage", "format", "caption", "is_partnership", "cta_keyword"])
        for n, p in enumerate(chosen, 1):
            w.writerow([n, p["permalink"], p["posted_london"], stage_names[p["stage_id"]], p["format"], p["caption"],
                        str(bool(p["is_partnership"])).lower(), p["cta_keyword"] or ""])

    print(f"\nwrote {len(chosen)} posts -> {OUT}")
    print("by stage (sample / eligible):")
    for s in sorted(by_stage):
        print(f"  {s} {stage_names[s]:<34} {sum(p['stage_id'] == s for p in chosen):>3} / {len(by_stage[s])}")
    for label, key in (("format", lambda p: p["format"]),
                       ("is_partnership", lambda p: bool(p["is_partnership"])),
                       ("cta_keyword set", lambda p: p["cta_keyword"] is not None)):
        smp, pop = Counter(key(p) for p in chosen), Counter(key(p) for p in eligible)
        print(f"by {label}: " + ", ".join(
            f"{k}: {smp.get(k, 0)} ({smp.get(k, 0) / len(chosen):.0%}) vs eligible {pop[k]} ({pop[k] / len(eligible):.0%})"
            for k in sorted(pop, key=str)))
    print("cta keywords in sample:", dict(Counter(p["cta_keyword"] for p in chosen if p["cta_keyword"]).most_common()))
    print("by stage x format:", dict(sorted(Counter((p["stage_id"], p["format"]) for p in chosen).items())))
    print("duplicate-group members in sample:", sum(1 for p in chosen if p["duplicate_group_id"]))


if __name__ == "__main__":
    main()
