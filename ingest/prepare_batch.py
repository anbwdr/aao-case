# /// script
# requires-python = ">=3.11"
# dependencies = ["duckdb>=1.1"]
# ///
"""Build one landing batch: CSV files plus _MANIFEST.json (PLAN.md §4 batch contract).

Plays the "nightly till-DB export". Kinds:
  initial   the four case tables + match results for the fixtures settled in the case
  ou        Over/Under 2.5 markets and options, no bets (step 14)
  scenario  the remaining match results: season over (step 14b, lands in raw_scenario)

The manifest lists each file's target table, columns and data-row count. The Workflow loads
every column as STRING and asserts the counts. Upload the manifest last: it is the trigger.

Usage: uv run ingest/prepare_batch.py KIND OUT_DIR
"""

from __future__ import annotations

import csv
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_ou_batch  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

CASE_TABLES = ["fixtures", "markets", "options", "bets"]
# extra.csv has ~130 columns, many with names BigQuery rejects (B365>2.5). Land the result subset.
MATCH_RESULT_COLUMNS = ["Div", "Date", "Time", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR"]
# The case's last settled kickoff. Batch 1 = results for fixtures scheduled on or before it.
LAST_SETTLED_KICKOFF = "2024-10-06"
TEAM_MAP = make_ou_batch.TEAM_MAP


def write_match_results(out_dir: Path, settled_part: bool) -> None:
    """Results joined to fixtures on mapped teams, filtered on the *case* kickoff date (never the
    extra.csv date: 108 fixtures were rescheduled)."""
    con = duckdb.connect()
    cols = ", ".join(f'e."{c}"' for c in MATCH_RESULT_COLUMNS)
    rows = con.execute(f"""
        select {cols}, cast(f.starting_time as date) <= date '{LAST_SETTLED_KICKOFF}' as in_settled_part, m.result_option_id
        from read_csv('{DATA}/extra.csv', all_varchar = true) e
        join read_csv('{DATA}/fixtures.csv') f
          on {TEAM_MAP.format(col='e."HomeTeam"')} = f.home_team
         and {TEAM_MAP.format(col='e."AwayTeam"')} = f.away_team
        join read_csv('{DATA}/markets.csv') m on m.fixture_id = f.id and m.name = '3-way'
        order by f.starting_time, f.id
    """).fetchall()
    total = con.execute(f"select count(*) from read_csv('{DATA}/extra.csv')").fetchone()[0]
    if len(rows) != total:
        sys.exit(f"match results: only {len(rows)} of {total} rows joined to a fixture")

    selected = [r for r in rows if r[-2] == settled_part]
    if settled_part:
        # Batch 1 must be exactly the fixtures the case has settled.
        unsettled = [r for r in selected if r[-1] == -999]
        settled_total = sum(1 for r in rows if r[-1] != -999)
        if unsettled or len(selected) != settled_total:
            sys.exit(f"batch 1 results: {len(selected)} rows, {len(unsettled)} unsettled in the case, "
                     f"case has {settled_total} settled")
    with open(out_dir / "match_results.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(MATCH_RESULT_COLUMNS)
        writer.writerows(r[: len(MATCH_RESULT_COLUMNS)] for r in selected)


def manifest_entry(path: Path) -> dict:
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        columns = next(reader)
        rows = sum(1 for _ in reader)
    return {"table": path.stem, "file": path.name, "columns": columns, "rows": rows}


def build(kind: str, out_dir: Path, batch_id: str | None = None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    if kind == "initial":
        for table in CASE_TABLES:
            shutil.copyfile(DATA / f"{table}.csv", out_dir / f"{table}.csv")
        write_match_results(out_dir, settled_part=True)
    elif kind == "ou":
        make_ou_batch.build(out_dir)
    elif kind == "scenario":
        write_match_results(out_dir, settled_part=False)
    else:
        sys.exit(f"unknown kind {kind!r}: initial, ou or scenario")

    batch_id = batch_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    manifest = {
        "batch_id": batch_id,
        "kind": kind,
        "scenario": kind == "scenario",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "files": [manifest_entry(p) for p in sorted(out_dir.glob("*.csv"))],
    }
    (out_dir / "_MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    m = build(sys.argv[1], Path(sys.argv[2]))
    for f in m["files"]:
        print(f"  {f['file']:<20} {f['rows']:>10,} rows")
    print(f"batch {m['batch_id']} ({m['kind']}) ready in {sys.argv[2]}")
