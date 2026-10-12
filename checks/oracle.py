# /// script
# requires-python = ">=3.11"
# dependencies = ["duckdb>=1.1"]
# ///
"""Reference implementation of the AAO KPIs over the raw CSVs in data/.

Shares no code with Dataform on purpose: it is the independent oracle that
`make verify` diffs BigQuery against. Writes checks/golden/*.csv and exits
non-zero if the PLAN.md §2 reference numbers or the 70/70 result cross-check
are not reproduced.

Usage: uv run checks/oracle.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
GOLDEN = ROOT / "checks" / "golden"

# Sample of markets for the option-cumulative golden file: settled and open.
SAMPLE_MARKET_IDS = (2, 3, 40, 71, 72, 100, 200, 381)

# PLAN.md §2. (value, absolute tolerance)
REFERENCE = {
    "settled_stake": (9_524_903, 1),
    "settled_earnings": (603_336, 1),
    "settled_margin": (0.0633, 0.00005),
    "open_bets": (16_105_242, 1),
    "worst_case_future": (-862_724, 1),
    "best_case_future": (3_953_924, 1),
}

# extra.csv (football-data.co.uk) name -> case fixtures name.
TEAM_MAP = {"Man United": "Man Utd", "Tottenham": "Spurs"}

FTR_TO_OPTION = {"H": "1", "D": "X", "A": "2"}


def build(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(f"""
        create table bets as select * from read_csv('{DATA}/bets.csv');
        create table options as select * from read_csv('{DATA}/options.csv');
        create table markets as select * from read_csv('{DATA}/markets.csv');
        create table fixtures as select * from read_csv('{DATA}/fixtures.csv');
        create table extra as
            select HomeTeam as home_team, AwayTeam as away_team, Date as match_date, FTR as ftr
            from read_csv('{DATA}/extra.csv', header = true, dateformat = '%d/%m/%Y');
    """)
    con.execute("create table team_map (source_name varchar, case_name varchar)")
    con.executemany("insert into team_map values (?, ?)", list(TEAM_MAP.items()))
    con.execute("create table ftr_map (ftr varchar, option_name varchar)")
    con.executemany("insert into ftr_map values (?, ?)", list(FTR_TO_OPTION.items()))

    con.execute("""
        create table as_of as select max(date) as as_of_date from bets;

        -- One row per bet with everything needed downstream.
        create table bet_facts as
        select
            b.id as bet_id,
            b.date as bet_date,
            b.option_id,
            b.odds,
            b.amount,
            b.odds * b.amount as potential_payout,
            o.market_id,
            m.fixture_id,
            m.result_option_id <> -999 as is_settled,
            case
                when m.result_option_id = -999 then null
                when b.option_id = m.result_option_id then b.amount - b.odds * b.amount
                else b.amount
            end as settled_earnings
        from bets b
        join options o on o.id = b.option_id
        join markets m on m.id = o.market_id;

        -- Earnings for AAO if option k of market m is the outcome, all bets to date.
        create table option_final as
        select
            o.market_id,
            o.id as option_id,
            ms.stake - coalesce(op.payout, 0) as earnings_if_realised
        from options o
        join (select market_id, sum(amount) as stake from bet_facts group by 1) ms
            on ms.market_id = o.market_id
        left join (select option_id, sum(potential_payout) as payout from bet_facts group by 1) op
            on op.option_id = o.id;

        create table market_summary as
        select
            m.id as market_id,
            m.name as market_name,
            f.id as fixture_id,
            f.home_team || ' v ' || f.away_team as fixture_label,
            f.location,
            cast(f.starting_time as date) as fixture_start_date,
            m.result_option_id <> -999 as is_settled,
            ro.name as result_option_name,
            s.total_stake,
            s.settled_earnings,
            s.settled_earnings / nullif(s.settled_stake, 0) as settled_margin,
            s.open_bets,
            case when m.result_option_id = -999 then w.worst_case end as worst_case,
            case when m.result_option_id = -999 then w.best_case end as best_case
        from markets m
        join fixtures f on f.id = m.fixture_id
        left join options ro on ro.id = m.result_option_id
        join (
            select
                market_id,
                sum(amount) as total_stake,
                sum(case when is_settled then amount end) as settled_stake,
                sum(settled_earnings) as settled_earnings,
                sum(case when not is_settled then amount end) as open_bets
            from bet_facts group by 1
        ) s on s.market_id = m.id
        join (
            select market_id, min(earnings_if_realised) as worst_case, max(earnings_if_realised) as best_case
            from option_final group by 1
        ) w on w.market_id = m.id;

        create table kpi_totals as
        select
            (select as_of_date from as_of) as as_of_date,
            sum(settled_earnings) as settled_earnings,
            sum(case when is_settled then total_stake end) as settled_stake,
            sum(settled_earnings) / sum(case when is_settled then total_stake end) as settled_margin,
            sum(open_bets) as open_bets,
            sum(worst_case) as worst_case_future,
            sum(best_case) as best_case_future
        from market_summary;

        -- Settled markets: case result vs actual result from extra.csv, joined on mapped teams.
        create table crosscheck as
        select
            m.id as market_id,
            ro.name as aao_result,
            fm.option_name as actual_result,
            ro.name = fm.option_name as match_flag
        from markets m
        join fixtures f on f.id = m.fixture_id
        join options ro on ro.id = m.result_option_id
        left join extra e
            on coalesce((select case_name from team_map where source_name = e.home_team), e.home_team) = f.home_team
           and coalesce((select case_name from team_map where source_name = e.away_team), e.away_team) = f.away_team
        left join ftr_map fm on fm.ftr = e.ftr
        where m.result_option_id <> -999;
    """)

    sample = ", ".join(str(i) for i in SAMPLE_MARKET_IDS)
    con.execute(f"""
        -- Dense market x option x bet date, from first to last bet date of the market.
        create table option_cumulative as
        with daily as (
            select market_id, option_id, bet_date,
                   sum(amount) as stake, sum(potential_payout) as payout
            from bet_facts where market_id in ({sample}) group by all
        ),
        bounds as (
            select market_id, min(bet_date) as d0, max(bet_date) as d1 from daily group by 1
        ),
        spine as (
            select b.market_id, o.id as option_id, o.name as option_name,
                   cast(unnest(generate_series(b.d0, b.d1, interval 1 day)) as date) as bet_date
            from bounds b join options o on o.market_id = b.market_id
        ),
        market_daily as (
            select market_id, bet_date, sum(stake) as stake from daily group by all
        )
        select
            s.market_id,
            s.option_id,
            s.option_name,
            s.bet_date,
            sum(coalesce(md.stake, 0)) over w as cum_stake_market,
            sum(coalesce(d.payout, 0)) over w as cum_payout_option,
            sum(coalesce(md.stake, 0)) over w - sum(coalesce(d.payout, 0)) over w as cum_earnings_if_realised,
            coalesce(s.option_id = m.result_option_id, false) as is_actual_outcome,
            m.result_option_id <> -999 as is_settled
        from spine s
        join markets m on m.id = s.market_id
        left join daily d using (market_id, option_id, bet_date)
        left join market_daily md using (market_id, bet_date)
        window w as (partition by s.market_id, s.option_id order by s.bet_date)
        order by 1, 2, 4;
    """)


def write_golden(con: duckdb.DuckDBPyConnection) -> None:
    GOLDEN.mkdir(parents=True, exist_ok=True)
    for table, order in [
        ("kpi_totals", "1"),
        ("market_summary", "market_id"),
        ("option_cumulative", "market_id, option_id, bet_date"),
        ("crosscheck", "market_id"),
    ]:
        con.execute(f"copy (select * from {table} order by {order}) to '{GOLDEN}/{table}.csv' (header)")


def check(con: duckdb.DuckDBPyConnection) -> list[str]:
    errors: list[str] = []
    row = con.execute("select * from kpi_totals").fetchone()
    cols = [d[0] for d in con.description]
    kpis = dict(zip(cols, row))

    print(f"as_of_date: {kpis['as_of_date']}")
    for name, (expected, tol) in REFERENCE.items():
        got = kpis[name]
        ok = abs(got - expected) <= tol
        print(f"  {'ok ' if ok else 'BAD'} {name:<20} {got:>16,.4f}  (ref {expected:,})")
        if not ok:
            errors.append(f"{name}: got {got}, expected {expected} ± {tol}")

    n_settled, n_match, n_unjoined = con.execute(
        "select count(*), count(*) filter (where match_flag), count(*) filter (where actual_result is null) from crosscheck"
    ).fetchone()
    print(f"crosscheck: {n_match}/{n_settled} settled markets agree with the actual result")
    if n_settled != 70 or n_match != 70 or n_unjoined:
        errors.append(f"crosscheck: {n_match}/{n_settled} agree, {n_unjoined} unjoined")

    # The per-market grain must reconcile to the totals.
    diff = con.execute("""
        select abs(sum(settled_earnings) - (select settled_earnings from kpi_totals)) from market_summary
    """).fetchone()[0]
    if diff > 0.01:
        errors.append(f"per-market settled earnings do not sum to total (diff {diff})")
    return errors


def main() -> int:
    con = duckdb.connect()
    build(con)
    write_golden(con)
    errors = check(con)
    if errors:
        print("\nORACLE FAILED:\n  " + "\n  ".join(errors), file=sys.stderr)
        return 1
    print(f"\noracle ok, golden files in {GOLDEN.relative_to(ROOT)}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
