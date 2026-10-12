# /// script
# requires-python = ">=3.11"
# dependencies = ["duckdb>=1.1", "sqlglot>=25"]
# ///
"""Run the compiled Dataform graph locally on DuckDB and diff it against the oracle.

A pre-pipeline check, no GCP needed:
  1. builds the landing batches with ingest/prepare_batch.py and lands them the way the
     Workflow does (all-STRING raw tables + metadata columns + raw.batch_manifest)
  2. compiles the Dataform project, transpiles each action from BigQuery SQL to DuckDB
     with sqlglot, and runs it in dependency order
  3. runs every assertion and diffs reporting.* against checks/golden/

It proves the model logic, not BigQuery itself: dialect differences are possible, so the
BigQuery diff in checks/verify.py stays the definition of done once the pipeline exists.

Usage: uv run checks/local_dataform.py [--ou] [--scenario] [--export-marts DIR]
  --ou        also land batch 2 (O/U 2.5): graded KPIs must not move, 760 markets
  --scenario  then land the scenario push and build the *_scenario datasets:
              graded datasets must not move, every market settled, open bets 0
  --export-marts  write the graded analytics.* tables to DIR/<table>.parquet (malloy/check.mjs)
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import duckdb
import sqlglot
from sqlglot import exp

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "checks"))
sys.path.insert(0, str(ROOT / "ingest"))
import golden_diff  # noqa: E402
import prepare_batch  # noqa: E402

DATAFORM_CLI = "@dataform/cli@3.0.71"


def compile_graph(scenario: bool) -> dict:
    cmd = ["npx", "-y", DATAFORM_CLI, "compile", "--json"]
    if scenario:
        cmd.append("--vars=scenario=true")
    out = subprocess.run(cmd, cwd=ROOT / "dataform", capture_output=True, text=True, check=True).stdout
    graph = json.loads(out)
    if graph.get("graphErrors"):
        sys.exit(f"compile errors: {graph['graphErrors']}")
    return graph


def land(con: duckdb.DuckDBPyConnection, kind: str, batch_id: str, workdir: Path) -> None:
    """What the Workflow does with one batch: STRING columns + metadata, counts, manifest rows."""
    batch_dir = workdir / batch_id
    manifest = prepare_batch.build(kind, batch_dir, batch_id)
    dataset = "raw_scenario" if manifest["scenario"] else "raw"
    for f in manifest["files"]:
        table = f"{dataset}.{f['table']}"
        cols = ", ".join(f'"{c}" varchar' for c in f["columns"])
        con.execute(f"""
            create table if not exists {table} ({cols}, _batch_id varchar, _ingested_at timestamp, _source_file varchar);
            insert into {table}
            select *, '{batch_id}', now(), 'gs://local/batch={batch_id}/{f['file']}'
            from read_csv('{batch_dir / f['file']}', all_varchar = true, header = true);
        """)
        landed = con.execute(f"select count(*) from {table} where _batch_id = '{batch_id}'").fetchone()[0]
        if landed != f["rows"]:
            sys.exit(f"{table}: landed {landed} rows, manifest says {f['rows']}")
        con.execute(f"insert into {dataset}.batch_manifest values ('{batch_id}', '{f['table']}', {f['rows']}, null, now())")
    print(f"landed {kind} batch {batch_id} into {dataset}: " + ", ".join(f"{f['table']} {f['rows']:,}" for f in manifest["files"]))


def to_duckdb(sql: str, project: str) -> str:
    sql = sql.replace(f"`{project}.", "`")
    tree = sqlglot.parse_one(sql, read="bigquery")
    # BigQuery NUMERIC is DECIMAL(38, 9); sqlglot would leave DuckDB's default (18, 3).
    for dtype in tree.find_all(exp.DataType):
        if dtype.this == exp.DataType.Type.DECIMAL and not dtype.expressions:
            dtype.replace(exp.DataType.build("DECIMAL(38, 9)"))
    # DuckDB's WEEK is already ISO.
    return re.sub(r"\bISOWEEK\b", "WEEK", tree.sql(dialect="duckdb"))


def target_name(target: dict) -> str:
    return f"{target['schema']}.{target['name']}"


def run_graph(con: duckdb.DuckDBPyConnection, graph: dict) -> list[str]:
    project = graph["projectConfig"]["defaultDatabase"]
    tables = {target_name(t["target"]): t for t in graph["tables"]}
    done: set[str] = {target_name(d["target"]) for d in graph["declarations"]}
    for schema in {t["target"]["schema"] for t in graph["tables"] + graph["assertions"]}:
        con.execute(f"create schema if not exists {schema}")

    def build(name: str) -> None:
        if name in done:
            return
        action = tables[name]
        for dep in action.get("dependencyTargets", []):
            build(target_name(dep))
        kind = "view" if action["type"] == "view" else "table"
        con.execute(f"create or replace {kind} {name} as {to_duckdb(action['query'], project)}")
        done.add(name)

    for name in tables:
        build(name)

    failures = []
    for assertion in graph["assertions"]:
        rows = con.execute(to_duckdb(assertion["query"], project)).fetchall()
        if rows:
            failures.append(f"assertion {target_name(assertion['target'])}: {len(rows)} row(s), first: {rows[0]}")
    print(f"built {len(tables)} actions, ran {len(graph['assertions'])} assertions, {len(failures)} failed")
    return failures


def runner(con: duckdb.DuckDBPyConnection) -> golden_diff.Runner:
    def run(sql: str) -> list[dict]:
        rel = con.execute(sql)
        cols = [d[0] for d in rel.description]
        return [dict(zip(cols, row)) for row in rel.fetchall()]
    return run


def scalar(con: duckdb.DuckDBPyConnection, sql: str) -> object:
    return con.execute(sql).fetchone()[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ou", action="store_true")
    parser.add_argument("--scenario", action="store_true")
    parser.add_argument("--export-marts", type=Path)
    args = parser.parse_args()

    con = duckdb.connect()
    con.execute("create schema raw; create schema raw_scenario")
    for ds in ["raw", "raw_scenario"]:
        con.execute(f"create table {ds}.batch_manifest (batch_id varchar, table_name varchar, row_count bigint, source_uri varchar, loaded_at timestamp)")
    errors: list[str] = []
    # Print assertion failures as they happen, before any later step can crash.
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        land(con, "initial", "20241016T230000Z", workdir)
        if args.ou:
            land(con, "ou", "20241017T230000Z", workdir)
        errors += run_graph(con, compile_graph(scenario=False))
        errors += golden_diff.diff_reporting(runner(con), "reporting")

        markets = scalar(con, "select count(*) from reporting.rpt_market_summary")
        expected_markets = 760 if args.ou else 380
        print(f"  {'ok ' if markets == expected_markets else 'BAD'} {markets} markets in rpt_market_summary")
        if markets != expected_markets:
            errors.append(f"expected {expected_markets} markets, got {markets}")
        if args.export_marts:
            args.export_marts.mkdir(parents=True, exist_ok=True)
            for (table,) in con.execute(
                "select table_name from information_schema.tables where table_schema = 'analytics'"
            ).fetchall():
                con.execute(f"copy analytics.{table} to '{args.export_marts / table}.parquet' (format parquet)")
            print(f"  exported analytics.* to {args.export_marts}")
        dq = con.execute("select count(*), coalesce(sum(stake), 0) from reporting.dq_bets_after_actual_match").fetchone()
        print(f"  warning (non-blocking): {dq[0]} fixture-days with bets after the actual match, stake {dq[1]:,.2f}")

        if args.scenario:
            land(con, "scenario", "20241018T090000Z", workdir)
            errors += run_graph(con, compile_graph(scenario=True))
            # Graded datasets are untouched by construction; re-diff to prove it.
            errors += golden_diff.diff_reporting(runner(con), "reporting")
            settled, total, open_bets = con.execute(
                "select count(*) filter (where is_settled), count(*), coalesce(sum(open_bets), 0) "
                "from reporting_scenario.rpt_market_summary"
            ).fetchone()
            ok = settled == total == expected_markets and open_bets == 0
            print(f"  {'ok ' if ok else 'BAD'} scenario: {settled}/{total} markets settled, open bets {open_bets}")
            if not ok:
                errors.append("scenario: not every market settled, or open bets left")
            print(con.sql("select as_of_date, settled_earnings, settled_margin, open_bets from reporting_scenario.rpt_kpi_totals"))

    if errors:
        print("\nLOCAL DATAFORM RUN FAILED:\n  " + "\n  ".join(errors), file=sys.stderr)
        return 1
    print("\nlocal dataform run ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
