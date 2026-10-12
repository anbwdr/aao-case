"""Compare query results with the oracle's golden files. Shared by local_dataform.py and verify.py."""

from __future__ import annotations

import csv
from collections.abc import Callable, Iterable
from pathlib import Path

GOLDEN = Path(__file__).resolve().parent / "golden"
TOLERANCE = 0.01

# golden file -> (reporting table, key columns, extra SQL filter)
# market_summary keeps to 3-way markets: other market types (O/U) are added on top and must
# leave the graded numbers untouched.
CHECKS = [
    ("kpi_totals", "rpt_kpi_totals", ["as_of_date"], ""),
    ("market_summary", "rpt_market_summary", ["market_id"], "market_name = '3-way'"),
    ("option_cumulative", "rpt_option_cumulative", ["option_id", "bet_date"], "market_id in ({sample})"),
    ("crosscheck", "rpt_crosscheck", ["market_id"], ""),
]

# Query runner: SQL -> list of dicts.
Runner = Callable[[str], list[dict]]


def load_golden(name: str) -> list[dict]:
    with open(GOLDEN / f"{name}.csv", newline="") as f:
        return list(csv.DictReader(f))


def same(golden: str, actual: object) -> bool:
    if actual is None or actual == "":
        return golden == ""
    if isinstance(actual, bool):
        return golden == str(actual).lower()
    try:
        return abs(float(golden) - float(actual)) <= TOLERANCE
    except (TypeError, ValueError):
        return golden == str(actual)


def diff_rows(expected: list[dict], actual: Iterable[dict], keys: list[str]) -> list[str]:
    actual_by_key = {tuple(str(r[k]) for k in keys): r for r in actual}
    mismatches = []
    if len(actual_by_key) != len(expected):
        mismatches.append(f"row count {len(actual_by_key)} != golden {len(expected)}")
    for exp_row in expected:
        key = tuple(exp_row[k] for k in keys)
        act_row = actual_by_key.get(key)
        if act_row is None:
            mismatches.append(f"{key}: missing")
            continue
        for col, value in exp_row.items():
            if not same(value, act_row.get(col)):
                mismatches.append(f"{key}.{col}: golden {value!r} vs {act_row.get(col)!r}")
    return mismatches


def diff_reporting(run: Runner, dataset: str) -> list[str]:
    """Diff every reporting table against its golden file; print one line per table."""
    errors = []
    for golden, table, keys, where in CHECKS:
        expected = load_golden(golden)
        if "{sample}" in where:  # golden holds a sample of markets only
            where = where.format(sample=", ".join(sorted({r["market_id"] for r in expected})))
        sql = f"select {', '.join(expected[0])} from {dataset}.{table}" + (f" where {where}" if where else "")
        mismatches = diff_rows(expected, run(sql), keys)
        print(f"  {'ok ' if not mismatches else 'BAD'} {dataset}.{table:<24} vs golden/{golden}.csv ({len(expected)} rows)")
        errors += [f"{table} {m}" for m in mismatches[:10]]
    return errors
