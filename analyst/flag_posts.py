"""Set the section 6 flags and cta_keyword on every row of content.content_posts.

Recomputes from scratch each run, so it is safe to re-run after new posts load:
- cta_keyword: first "Comment[:]? KEYWORD" in the caption whose word is on the
  confirmed allowlist, stored uppercase. Null if none.
- is_partnership: #ad, a hashtag ending in "partner", or "Partner" after a brand
  name (e.g. MitoQPartner, "#MitoQ Partner"). content.partnership_overrides wins.
- is_boosted: any snapshot with paid reach > 0 or spend > 0.
- duplicate_group_id: same format and same caption (lowercased, trimmed) posted
  within 7 days of another copy. A reel and a carousel never group. Copies are chained, so A-B-C each <= 7 days apart form one group.
  The id is derived from the group's permalinks, so it is stable across runs.
- in_data_gap: posted_at (London date) inside any content.data_gaps period.

Only these five columns (and updated_at) are written. stage_id, captions and
everything outside content_posts are left alone.

Usage:
    python3 analyst/flag_posts.py            # compute, run section 6 checks, write nothing
    python3 analyst/flag_posts.py --apply    # same, then write if every check passes
"""

import argparse
import json
import re
import secrets
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from db import run_sql

CTA_KEYWORDS = [
    "MASTERCLASS", "THRIVE", "REBUILT", "QUIZ", "WAITLIST", "VIP", "METHOD", "LEAN", "SUMMER",
    "BF24", "FRIDAY", "LIFT", "SUMMIT", "CHEATSHEET", "MENOSLEEP", "APPLY", "CREATINE", "STAGES",
    "STRONG", "BRAIN", "PROTEIN",
]
CTA_RE = re.compile(r"\bcomment:?\s+[\"'“”‘’]*(" + "|".join(CTA_KEYWORDS) + r")\b", re.I)

AD_RE = re.compile(r"(?<![\w#])#ad\b", re.I)
PARTNER_HASHTAG_RE = re.compile(r"#\w*partner\b", re.I)
BRAND_PARTNER_RE = re.compile(r"[A-Za-z0-9]\s?Partner\b")  # capital P only: not "your partner"

DUPLICATE_WINDOW = timedelta(days=7)
GROUP_NAMESPACE = uuid.UUID("5b1c2f0e-6d0a-4c3e-9a57-0f3c7e1d2a41")

# Section 6 test cases, from real data.
EXPECT_PARTNERSHIP = {
    "DdmDa4EMVEf": "22 Sep 2026 (Humann)",
    "DdJj_JOMLc1": "11 Sep 2026 (MitoQ)",
    "DcjLk1msrMh": "27 Aug 2026 (Ritual)",
}
EXPECT_DUPLICATE_GROUPS = {
    "5 Sep 2026 pair": {"Dc6yoxgMPh4", "Dc6ymlQMzEX"},
    "28 Aug 2026 triple": {"Dcmc3X1MHp2", "Dcmce6bs7aX", "DcmcQ11MkWH"},
}
# Same caption, 4 days apart, but a reel and a carousel: must not group.
EXPECT_NOT_DUPLICATE = {"DGEYo79s5Sg": "14 Feb 2025 reel", "DGOg-oetwDF": "18 Feb 2025 carousel"}


def cta_keyword(caption: str | None) -> str | None:
    m = CTA_RE.search(caption or "")
    return m.group(1).upper() if m else None


def detected_partnership(caption: str | None) -> bool:
    c = caption or ""
    return bool(AD_RE.search(c) or PARTNER_HASHTAG_RE.search(c) or BRAND_PARTNER_RE.search(c))


def duplicate_groups(posts: list[dict]) -> dict[str, uuid.UUID]:
    """Map post id -> group id for every post with a same-format, same-caption copy within 7 days."""
    by_caption = defaultdict(list)
    for p in posts:
        norm = (p["caption"] or "").lower().strip()
        if norm:
            by_caption[(p["format"], norm)].append(p)
    out = {}
    for copies in by_caption.values():
        copies.sort(key=lambda p: p["posted"])
        run = [copies[0]]
        for p in copies[1:] + [None]:
            if p is not None and p["posted"] - run[-1]["posted"] <= DUPLICATE_WINDOW:
                run.append(p)
                continue
            if len(run) > 1:
                gid = uuid.uuid5(GROUP_NAMESPACE, "|".join(sorted(x["permalink"] for x in run)))
                for x in run:
                    out[x["id"]] = gid
            run = [p]
    return out


def compute() -> tuple[list[dict], dict]:
    posts = run_sql("""
        select p.id, p.permalink, p.shortcode, p.format, p.caption, p.posted_at,
               (p.posted_at at time zone 'Europe/London')::date::text as london_date,
               coalesce(b.boosted, false) as boosted
        from content.content_posts p
        left join (
            select post_id, bool_or(coalesce(reach_paid, 0) > 0 or coalesce(spend, 0) > 0) as boosted
            from content.content_metrics_snapshots group by post_id
        ) b on b.post_id = p.id""")
    overrides = {r["permalink"]: r["is_partnership"] for r in
                 run_sql("select permalink, is_partnership from content.partnership_overrides")}
    gaps = run_sql("select start_date::text as s, end_date::text as e from content.data_gaps")

    for p in posts:
        p["posted"] = datetime.fromisoformat(p["posted_at"].replace(" ", "T").replace("+00", "+00:00"))
    groups = duplicate_groups(posts)

    rows = []
    for p in posts:
        detected = detected_partnership(p["caption"])
        rows.append({
            "id": p["id"],
            "shortcode": p["shortcode"],
            "format": p["format"],
            "cta_keyword": cta_keyword(p["caption"]),
            "detected_partnership": detected,
            "is_partnership": overrides.get(p["permalink"], detected),
            "is_boosted": bool(p["boosted"]),
            "duplicate_group_id": str(groups[p["id"]]) if p["id"] in groups else None,
            "in_data_gap": any(g["s"] <= p["london_date"] <= g["e"] for g in gaps),
        })
    context = {"overrides": overrides, "gaps": gaps}
    return rows, context


def check(rows: list[dict]) -> list[str]:
    """Section 6 test cases. Returns failures."""
    by_code = {r["shortcode"]: r for r in rows}
    failures = []
    for code, label in EXPECT_PARTNERSHIP.items():
        r = by_code.get(code)
        ok = bool(r and r["is_partnership"])
        print(f"  partnership  {label:<24} {code}: {'PASS' if ok else 'FAIL'}")
        if not ok:
            failures.append(f"partnership {label} ({code}) not flagged")
    for label, codes in EXPECT_DUPLICATE_GROUPS.items():
        gids = {by_code[c]["duplicate_group_id"] for c in codes if c in by_code}
        members = {c for c, r in by_code.items() if r["duplicate_group_id"] in gids} if gids else set()
        ok = len(gids) == 1 and None not in gids and members == codes
        print(f"  duplicate    {label:<24} {sorted(codes)}: {'PASS' if ok else 'FAIL'}"
              f" (group {next(iter(gids)) if len(gids) == 1 else gids}, members {sorted(members)})")
        if not ok:
            failures.append(f"duplicate {label} not grouped exactly")
    for code, label in EXPECT_NOT_DUPLICATE.items():
        ok = code in by_code and by_code[code]["duplicate_group_id"] is None
        print(f"  not a dup    {label:<24} {code}: {'PASS' if ok else 'FAIL'}")
        if not ok:
            failures.append(f"{label} ({code}) is in a duplicate group")
    formats = defaultdict(set)
    for r in rows:
        if r["duplicate_group_id"]:
            formats[r["duplicate_group_id"]].add(r["format"])
    mixed = [g for g, f in formats.items() if len(f) > 1]
    print(f"  no group mixes formats: {'PASS' if not mixed else 'FAIL ' + str(mixed)}")
    if mixed:
        failures.append(f"groups mixing formats: {mixed}")
    return failures


def apply(rows: list[dict]) -> dict:
    payload = json.dumps([{k: r[k] for k in ("id", "cta_keyword", "is_partnership", "is_boosted",
                                             "duplicate_group_id", "in_data_gap")} for r in rows])
    tag = f"f_{secrets.token_hex(8)}"
    return run_sql(f"""
with src as (
    select * from jsonb_to_recordset(${tag}${payload}${tag}$::jsonb) as t(
        id uuid, cta_keyword text, is_partnership boolean, is_boosted boolean,
        duplicate_group_id uuid, in_data_gap boolean)
),
updated as (
    update content.content_posts p
       set cta_keyword        = s.cta_keyword,
           is_partnership     = s.is_partnership,
           is_boosted         = s.is_boosted,
           duplicate_group_id = s.duplicate_group_id,
           in_data_gap        = s.in_data_gap,
           updated_at         = now()
      from src s
     where p.id = s.id
    returning p.id
)
select count(*) as updated from updated;
""")[0]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true", help="write the flags if every check passes")
    a = p.parse_args()

    rows, ctx = compute()
    print(f"posts: {len(rows)} | partnership overrides: {len(ctx['overrides'])} | data gaps: {ctx['gaps']}")
    print("section 6 checks:")
    failures = check(rows)

    kw = Counter(r["cta_keyword"] for r in rows if r["cta_keyword"])
    groups = Counter(r["duplicate_group_id"] for r in rows if r["duplicate_group_id"])
    print(f"cta_keyword set: {sum(kw.values())} posts, {dict(kw.most_common())}")
    print(f"is_partnership: {sum(r['is_partnership'] for r in rows)} "
          f"(detected {sum(r['detected_partnership'] for r in rows)}, overrides applied {len(ctx['overrides'])})")
    print(f"is_boosted: {sum(r['is_boosted'] for r in rows)}")
    print(f"duplicate groups: {len(groups)} covering {sum(groups.values())} posts, sizes {sorted(groups.values())}")
    print(f"in_data_gap: {sum(r['in_data_gap'] for r in rows)}")

    if failures:
        raise SystemExit("NOT APPLIED, checks failed:\n  " + "\n  ".join(failures))
    if a.apply:
        print(f"written: {apply(rows)['updated']} rows")
    else:
        print("dry run: nothing written (use --apply)")


if __name__ == "__main__":
    main()
