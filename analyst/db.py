"""Run SQL against the linked wtm-attribution project via the Supabase CLI.

Uses `supabase db query --linked`, which authenticates with the CLI's existing
login, so no database password is stored anywhere in this repo.
"""

import json
import subprocess
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def run_sql(sql: str) -> list[dict]:
    """Execute one SQL script and return the rows of its final statement."""
    with tempfile.NamedTemporaryFile("w", suffix=".sql", encoding="utf-8", delete=False) as f:
        f.write(sql)
        path = f.name
    try:
        proc = subprocess.run(
            ["supabase", "db", "query", "--linked", "-f", path],
            cwd=REPO, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600,
        )
    finally:
        Path(path).unlink(missing_ok=True)
    out = proc.stdout
    if proc.returncode != 0 or "{" not in out:
        raise RuntimeError(f"supabase db query failed ({proc.returncode}): {(proc.stderr or out).strip()[-1000:]}")
    return json.loads(out[out.index("{"):])["rows"]
