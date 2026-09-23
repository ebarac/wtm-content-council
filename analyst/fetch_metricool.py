"""Fetch one Metricool Instagram connector for a date range and save the raw response.

Metricool is only reachable through the claude.ai MCP connector, so this runs a
headless `claude -p` session that makes exactly one tool call. The raw tool
result is taken from Claude Code's stream-json event log and written to disk by
this script, byte for byte; no model ever retypes it (spec section 3, Q1).

The tool call's arguments are checked against what was requested, so a wrong
date range or field list fails the fetch instead of producing a bad file.

Usage:
    python3 analyst/fetch_metricool.py reels 2026-08-23 2026-09-21
    python3 analyst/fetch_metricool.py posts 2026-08-23 2026-09-21
"""

import argparse
import json
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, time as dtime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BRAND_ID = "3304707"
TZ = ZoneInfo("Europe/London")
TOOL = "mcp__claude_ai_Metricool_Social_Media_Management__getAnalyticsDataByMetrics"
RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"

# Field order here is the column order of every returned row.
FIELDS = {
    "reels": ["IGRE02", "IGRE03", "IGRE06", "IGRE11", "IGRE23", "IGRE12", "IGRE21",
              "IGRE07", "IGRE24", "IGRE28", "IGRE16", "IGRE20", "IGRE10"],
    "posts": ["IGPO01", "IGPO02", "IGPO03", "IGPO04", "IGPO05", "IGPO06", "IGPO07",
              "IGPO08", "IGPO10", "IGPO12", "IGPO13", "IGPO14", "IGPO15", "IGPO19",
              "IGPO26", "IGPO27", "IGPO28", "IGPO29"],
}

ATTEMPTS = 5


def london_bounds(start: date, end: date) -> tuple[str, str]:
    """Whole days in Europe/London, as ISO 8601 with the correct offset for each end."""
    lo = datetime.combine(start, dtime(0, 0, 0), TZ)
    hi = datetime.combine(end, dtime(23, 59, 59), TZ)
    return lo.isoformat(), hi.isoformat()


def run_headless(args: dict, model: str) -> list[dict]:
    prompt = (
        f"Call the tool {TOOL} exactly once with exactly these arguments, "
        f"copied verbatim, and nothing else:\n{json.dumps(args)}\n"
        "Do not call any other Metricool tool. After the call, reply with only the word done."
    )
    with tempfile.TemporaryDirectory() as cwd:
        proc = subprocess.run(
            ["claude", "-p", prompt,
             "--model", model,
             "--allowedTools", TOOL,
             "--output-format", "stream-json", "--verbose",
             "--max-turns", "4"],
            cwd=cwd, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600,
        )
    if proc.returncode != 0:
        raise RuntimeError(f"claude -p exited {proc.returncode}: {proc.stderr.strip()[-500:]}")
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


def extract_result(events: list[dict], expected_args: dict) -> str:
    """Return the raw text of the single Metricool tool result, after checking its call."""
    calls = {}
    results = {}
    for e in events:
        message = e.get("message")
        if not isinstance(message, dict):  # some event types carry a plain-string message
            continue
        content = message.get("content", [])
        if not isinstance(content, list):
            continue
        for c in content:
            if not isinstance(c, dict):
                continue
            if e["type"] == "assistant" and c.get("type") == "tool_use" and c.get("name") == TOOL:
                calls[c["id"]] = c["input"]
            elif e["type"] == "user" and c.get("type") == "tool_result":
                results[c["tool_use_id"]] = c

    if len(calls) != 1:
        raise RuntimeError(f"expected exactly 1 Metricool call, got {len(calls)}")
    call_id, call_args = next(iter(calls.items()))
    if call_args != expected_args:
        raise RuntimeError(f"tool called with wrong arguments:\n  asked {expected_args}\n  got   {call_args}")

    result = results.get(call_id)
    if result is None:
        raise RuntimeError("no tool result recorded for the Metricool call")
    body = result["content"]
    text = body if isinstance(body, str) else "".join(
        part.get("text", "") for part in body if part.get("type") == "text")
    if result.get("is_error"):
        raise RuntimeError(f"Metricool returned an error: {text[:300]}")
    return text


def validate(text: str, n_fields: int) -> int:
    """The raw text must be complete JSON with fixed-width rows. Returns the row count."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"response is not complete JSON (truncated or error page?): {exc}") from exc
    rows = data.get("rows")
    if not isinstance(rows, list):
        raise RuntimeError(f"response has no 'rows' list: {text[:300]}")
    bad = [i for i, r in enumerate(rows) if len(r) != n_fields]
    if bad:
        raise RuntimeError(f"{len(bad)} rows do not have {n_fields} columns (first bad row {bad[0]})")
    return len(rows)


def fetch(connector: str, start: date, end: date, model: str) -> Path:
    fields = FIELDS[connector]
    lo, hi = london_bounds(start, end)
    expected = {"brandId": BRAND_ID, "from": lo, "to": hi, "metrics": fields}

    last_error = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            fetched_at = datetime.now(timezone.utc)
            text = extract_result(run_headless(expected, model), expected)
            n_rows = validate(text, len(fields))
            break
        except Exception as exc:  # noqa: BLE001 - every failure is retried, then reported
            last_error = exc
            print(f"[{connector} {start}..{end}] attempt {attempt}/{ATTEMPTS} failed: {exc}", file=sys.stderr, flush=True)
            if attempt < ATTEMPTS:
                time.sleep(10 * 2 ** (attempt - 1))
    else:
        raise SystemExit(f"FAILED {connector} {start}..{end} after {ATTEMPTS} attempts: {last_error}")

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    stem = f"instagram_{connector}_{start}_{end}"
    raw_path = RAW_DIR / f"{stem}.json"
    raw_path.write_text(text, encoding="utf-8")
    meta = {
        "connector": connector,
        "brand_id": BRAND_ID,
        "from": lo,
        "to": hi,
        "fields": fields,
        "fetched_at": fetched_at.isoformat(),
        "row_count": n_rows,
        "raw_file": raw_path.name,
    }
    (RAW_DIR / f"{stem}.meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(f"[{connector} {start}..{end}] saved {n_rows} rows on attempt {attempt}/{ATTEMPTS} -> {raw_path}", flush=True)
    return raw_path


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("connector", choices=sorted(FIELDS))
    p.add_argument("start", type=date.fromisoformat)
    p.add_argument("end", type=date.fromisoformat)
    p.add_argument("--model", default="haiku", help="model that issues the tool call (arguments are verified)")
    a = p.parse_args()
    if a.end < a.start:
        p.error("end is before start")
    fetch(a.connector, a.start, a.end, a.model)


if __name__ == "__main__":
    main()
