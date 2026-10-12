# /// script
# requires-python = ">=3.11"
# dependencies = ["duckdb>=1.1"]
# ///
"""Build an Over/Under 2.5 goals market for every fixture (PLAN.md §9 step 14).

Structure only, no bets: it proves the model is at market grain (the graded KPIs must not
move) and demos an incremental batch. Ids are offset so they can never collide with the
case's ids (markets 2-381, options 1-1140). latest_odds come from extra.csv (Bet365 O/U 2.5).
The source system has not settled these markets, so result_option_id is -999 for all;
base settles them from match_results (FTHG + FTAG), like the 3-way markets.

Usage: uv run ingest/make_ou_batch.py OUT_DIR   (writes markets.csv and options.csv)
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

MARKET_NAME = "Over/Under 2.5"  # base_markets settles on this exact name
MARKET_ID_OFFSET = 10_000  # market id = offset + fixture id
OPTION_ID_OFFSET = 20_000  # option ids = offset + 2 * fixture id (+1 for Under)
UNSETTLED = -999

# extra.csv name -> case fixtures name. The pipeline's mapping lives in base_team_name_map;
# this copy only lines up the odds while building the demo batch.
TEAM_MAP = "case {col} when 'Man United' then 'Man Utd' when 'Tottenham' then 'Spurs' else {col} end"


def build(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"""
        create table ou as
        select
            f.id as fixture_id,
            e."B365>2.5" as over_odds,
            e."B365<2.5" as under_odds
        from read_csv('{DATA}/fixtures.csv') f
        join read_csv('{DATA}/extra.csv', all_varchar = true) e
          on {TEAM_MAP.format(col='e."HomeTeam"')} = f.home_team
         and {TEAM_MAP.format(col='e."AwayTeam"')} = f.away_team
    """)
    fixtures = con.execute(f"select count(*) from read_csv('{DATA}/fixtures.csv')").fetchone()[0]
    joined = con.execute("select count(*) from ou").fetchone()[0]
    if joined != fixtures:
        sys.exit(f"O/U batch: {joined} of {fixtures} fixtures matched extra.csv")

    con.execute(f"""
        copy (
            select {MARKET_ID_OFFSET} + fixture_id as id, fixture_id, '{MARKET_NAME}' as name,
                   {UNSETTLED} as result_option_id
            from ou order by fixture_id
        ) to '{out_dir}/markets.csv' (header);
        copy (
            select id, market_id, name, latest_odds from (
                select {OPTION_ID_OFFSET} + 2 * fixture_id as id, {MARKET_ID_OFFSET} + fixture_id as market_id,
                       'Over' as name, over_odds as latest_odds, fixture_id, 0 as k
                from ou
                union all
                select {OPTION_ID_OFFSET} + 2 * fixture_id + 1, {MARKET_ID_OFFSET} + fixture_id,
                       'Under', under_odds, fixture_id, 1
                from ou
            ) order by fixture_id, k
        ) to '{out_dir}/options.csv' (header);
    """)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    build(Path(sys.argv[1]))
    print(f"O/U 2.5 markets and options written to {sys.argv[1]}")
