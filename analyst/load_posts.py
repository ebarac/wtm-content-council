"""Load saved Metricool files into content.content_posts and content.content_metrics_snapshots.

Reads a raw file written by fetch_metricool.py plus its .meta.json (which records
the field order and when the data was captured). Each file is loaded in a single
SQL statement, so it either loads completely or not at all.

- content_posts is upserted on permalink. On conflict only caption, posted_at and
  format are updated; flags are left alone, and stage_id is only filled if empty.
- One snapshot per post, captured_at = the file's fetch time. Reloading the same
  file does not add duplicate snapshots.
- is_partnership, is_boosted, duplicate_group_id and in_data_gap are not set here
  (build step 3).

Usage:
    python3 analyst/load_posts.py data/raw/instagram_reels_2026-08-23_2026-09-21.json [more files...]
"""

import argparse
import json
import re
import secrets
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from db import run_sql

TZ = ZoneInfo("Europe/London")

# content_metrics_snapshots column -> Metricool field, per connector (spec sections 3 and 4).
METRICS = {
    "reels": {
        "reach": "IGRE11", "views": "IGRE23", "saves": "IGRE12", "shares": "IGRE21",
        "comments": "IGRE07", "likes": None, "view_rate": "IGRE28", "avg_watch_time": "IGRE24",
        "reach_paid": "IGRE16", "spend": "IGRE20",
    },
    "posts": {
        "reach": "IGPO14", "views": "IGPO28", "saves": "IGPO15", "shares": "IGPO27",
        "comments": "IGPO08", "likes": "IGPO13", "view_rate": None, "avg_watch_time": None,
        "reach_paid": "IGPO19", "spend": "IGPO26",
    },
}
POST_FIELDS = {
    "reels": {"posted": "IGRE02", "caption": "IGRE03", "url": "IGRE06"},
    "posts": {"posted": "IGPO02", "caption": "IGPO03", "url": "IGPO06", "type": "IGPO07"},
}
INT_COLUMNS = {"reach", "views", "saves", "shares", "comments", "likes", "reach_paid"}

PERMALINK_RE = re.compile(r"^https://www\.instagram\.com/(reel|p|tv)/([A-Za-z0-9_-]+)/$")


def normalise_permalink(url: str) -> tuple[str, str]:
    """Strip the /whatthemenopause/ segment, query string and fragment. Returns (permalink, shortcode)."""
    clean = url.strip().split("?")[0].split("#")[0]
    clean = clean.replace("/whatthemenopause/", "/")
    if not clean.endswith("/"):
        clean += "/"
    m = PERMALINK_RE.match(clean)
    if not m:
        raise ValueError(f"unrecognised permalink: {url!r}")
    return clean, m.group(2)


def post_format(connector: str, row: dict) -> str:
    if connector == "reels":
        return "reel"
    kind = row[POST_FIELDS["posts"]["type"]]
    if kind == "FEED_CAROUSEL_ALBUM":
        return "carousel"
    if kind == "FEED_IMAGE":
        return "image"
    raise ValueError(f"unknown IGPO07 type {kind!r} for {row[POST_FIELDS['posts']['url']]}")


def posted_at(value: str) -> str:
    """Metricool's yyyymmddHHMMSS is local time in the brand's timezone (Europe/London)."""
    local = datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=TZ)
    return local.astimezone(timezone.utc).isoformat()


def number(value, column: str):
    if value is None or value == "":
        return None
    d = Decimal(str(value))
    if column in INT_COLUMNS:
        if d != d.to_integral_value():
            raise ValueError(f"{column} expected a whole number, got {value!r}")
        return int(d)
    return str(d)  # numeric columns: pass as text to keep full precision


def build_records(raw_path: Path) -> tuple[dict, list[dict]]:
    meta = json.loads(raw_path.with_name(raw_path.stem + ".meta.json").read_text(encoding="utf-8"))
    connector, fields = meta["connector"], meta["fields"]
    rows = json.loads(raw_path.read_text(encoding="utf-8"))["rows"]

    records, seen = [], {}
    for values in rows:
        if len(values) != len(fields):
            raise ValueError(f"row has {len(values)} columns, expected {len(fields)}")
        row = dict(zip(fields, values))
        f = POST_FIELDS[connector]
        permalink, shortcode = normalise_permalink(row[f["url"]])
        if permalink in seen:
            raise ValueError(f"permalink appears twice in {raw_path.name}: {permalink}")
        seen[permalink] = True
        rec = {
            "permalink": permalink,
            "shortcode": shortcode,
            "format": post_format(connector, row),
            "posted_at": posted_at(row[f["posted"]]),
            "caption": row[f["caption"]],
            "raw_json": {"connector": connector, "source_file": raw_path.name, "fields": row},
        }
        for column, field in METRICS[connector].items():
            rec[column] = number(row[field], column) if field else None
        records.append(rec)
    return meta, records


def load_sql(records: list[dict], captured_at: str) -> str:
    tag = f"src_{secrets.token_hex(8)}"
    payload = json.dumps(records, ensure_ascii=False)
    if f"${tag}$" in payload:
        raise RuntimeError("dollar-quote tag collision")
    return f"""
with src as (
    select *
    from jsonb_to_recordset(${tag}${payload}${tag}$::jsonb) as t(
        permalink text, shortcode text, format text, posted_at timestamptz, caption text,
        reach bigint, views bigint, saves bigint, shares bigint, comments bigint, likes bigint,
        view_rate numeric, avg_watch_time numeric, reach_paid bigint, spend numeric,
        raw_json jsonb)
),
staged as (
    select s.*,
           (select st.id from content.account_stages st
             where (s.posted_at at time zone 'Europe/London')::date
                   between st.start_date and coalesce(st.end_date, 'infinity'::date)) as stage_id
    from src s
),
upserted as (
    insert into content.content_posts (permalink, shortcode, format, posted_at, caption, stage_id)
    select permalink, shortcode, format, posted_at, caption, stage_id from staged
    on conflict (permalink) do update
        set caption    = excluded.caption,
            posted_at  = excluded.posted_at,
            format     = excluded.format,
            stage_id   = coalesce(content.content_posts.stage_id, excluded.stage_id),
            updated_at = now()
    returning id, permalink
),
snapshots as (
    insert into content.content_metrics_snapshots
        (post_id, captured_at, reach, views, saves, shares, comments, likes,
         view_rate, avg_watch_time, reach_paid, spend, raw_json)
    select u.id, '{captured_at}'::timestamptz, s.reach, s.views, s.saves, s.shares, s.comments, s.likes,
           s.view_rate, s.avg_watch_time, s.reach_paid, s.spend, s.raw_json
    from upserted u join src s using (permalink)
    on conflict (post_id, captured_at) do nothing
    returning post_id
)
select (select count(*) from upserted) as posts_upserted,
       (select count(*) from snapshots) as snapshots_inserted;
"""


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("files", nargs="+", type=Path)
    a = p.parse_args()
    for raw_path in a.files:
        meta, records = build_records(raw_path)
        captured_at = datetime.fromisoformat(meta["fetched_at"]).isoformat()
        result = run_sql(load_sql(records, captured_at))[0]
        print(f"{raw_path.name}: {len(records)} rows parsed, "
              f"{result['posts_upserted']} posts upserted, {result['snapshots_inserted']} snapshots inserted")


if __name__ == "__main__":
    main()
