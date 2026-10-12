# /// script
# requires-python = ">=3.11"
# dependencies = ["google-cloud-bigquery>=3.27", "gspread>=6.1", "google-auth>=2.37"]
# ///
"""Cloud half of `make verify` (PLAN.md §6): BigQuery vs golden, Sheet vs BigQuery.

  1. reporting.* in BigQuery diffed against checks/golden/ (absolute tolerance 0.01)
  2. every published Sheet tab diffed against its BigQuery table, cell by cell
  3. dq_bets_after_actual_match printed as a warning, never a failure

Graded datasets only, never *_scenario. A stage that is not set up yet (no infra/config.env,
no Sheet) is reported as SKIPPED, loudly: a step that needs it is not done until it ran.
The Sheet is read as the publish service account (impersonation, granted by infra/setup.sh).

Usage: uv run checks/verify.py
"""

from __future__ import annotations

import datetime as dt
import decimal
import sys
from pathlib import Path

import google.auth
import gspread
from google.auth import impersonated_credentials
from google.cloud import bigquery

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "checks"))
sys.path.insert(0, str(ROOT / "publish"))
import golden_diff  # noqa: E402
from publish import META_TAB, TABLES  # noqa: E402

SHEET_EPOCH = dt.date(1899, 12, 30)  # Sheets serial date 0


def read_config() -> dict:
    path = ROOT / "infra" / "config.env"
    if not path.exists():
        return {}
    pairs = (line.split("=", 1) for line in path.read_text().splitlines() if "=" in line and not line.startswith("#"))
    return {k.strip(): v.strip().strip('"') for k, v in pairs}


def bq_runner(client: bigquery.Client) -> golden_diff.Runner:
    def run(sql: str) -> list[dict]:
        sql = sql.replace(" from reporting.", f" from `{client.project}.reporting`.")
        return [dict(row.items()) for row in client.query(sql).result()]
    return run


def sheet_value(value: object) -> object:
    """BigQuery value -> what the Sheets API returns (UNFORMATTED_VALUE, SERIAL_NUMBER)."""
    if value is None:
        return ""
    if isinstance(value, dt.datetime):
        value = value.date()
    if isinstance(value, dt.date):
        return (value - SHEET_EPOCH).days
    if isinstance(value, decimal.Decimal):
        return float(value)
    return value


def same_cell(expected: object, actual: object) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return isinstance(expected, bool) and isinstance(actual, bool) and expected == actual
    if isinstance(expected, str):  # "1" must stay text, not become the number 1
        return isinstance(actual, str) and expected == actual
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return abs(expected - actual) <= golden_diff.TOLERANCE
    return str(expected) == str(actual)


def diff_sheet(client: bigquery.Client, sheet: gspread.Spreadsheet) -> list[str]:
    errors = []
    titles = list(TABLES)
    response = sheet.values_batch_get(
        titles, params={"valueRenderOption": "UNFORMATTED_VALUE", "dateTimeRenderOption": "SERIAL_NUMBER"}
    )
    for title, value_range in zip(titles, response["valueRanges"]):
        values = value_range.get("values", [])
        result = client.query(f"select * from `{client.project}.reporting.{title}` order by {TABLES[title]}").result()
        header = [f.name for f in result.schema]
        expected = [[sheet_value(v) for v in row.values()] for row in result]
        mismatches = []
        if not values or values[0] != header:
            mismatches.append(f"header {values[0] if values else None} != {header}")
        rows = values[1:]
        if len(rows) != len(expected):
            mismatches.append(f"{len(rows)} rows in the Sheet, {len(expected)} in BigQuery")
        for i, (exp_row, act_row) in enumerate(zip(expected, rows), start=2):
            act_row = act_row + [""] * (len(exp_row) - len(act_row))  # the API trims trailing blanks
            for col, e, a in zip(header, exp_row, act_row):
                if not same_cell(e, a):
                    mismatches.append(f"row {i} {col}: BigQuery {e!r} vs Sheet {a!r}")
        print(f"  {'ok ' if not mismatches else 'BAD'} Sheet tab {title:<28} vs BigQuery ({len(expected)} rows)")
        errors += [f"Sheet {title}: {m}" for m in mismatches[:10]]
    return errors


def main() -> int:
    config = read_config()
    errors: list[str] = []
    skipped: list[str] = []

    if not config.get("PROJECT_ID"):
        skipped += ["BigQuery vs golden (no infra/config.env)", "Sheet vs BigQuery (no infra/config.env)"]
    else:
        project = config["PROJECT_ID"]
        credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        client = bigquery.Client(project=project, credentials=credentials, location=config.get("BQ_LOCATION", "EU"))

        print(f"BigQuery {project}.reporting vs golden:")
        errors += golden_diff.diff_reporting(bq_runner(client), "reporting")
        dq = next(iter(client.query(
            f"select count(*), coalesce(sum(stake), 0) from `{project}.reporting.dq_bets_after_actual_match`"
        ).result()))
        print(f"  warning (non-blocking): {dq[0]} fixture-days with bets after the actual match, stake {dq[1]:,.2f}")

        if not config.get("SHEET_ID"):
            skipped.append("Sheet vs BigQuery (no SHEET_ID)")
        else:
            sa = f"{config.get('PUBLISH_SA', 'aao-publish')}@{project}.iam.gserviceaccount.com"
            sheet_credentials = impersonated_credentials.Credentials(
                source_credentials=credentials,
                target_principal=sa,
                target_scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
            )
            sheet = gspread.authorize(sheet_credentials).open_by_key(config["SHEET_ID"])
            if META_TAB not in [w.title for w in sheet.worksheets()]:
                skipped.append("Sheet vs BigQuery (nothing published yet)")
            else:
                print("Sheet vs BigQuery:")
                errors += diff_sheet(client, sheet)

    for stage in skipped:
        print(f"  SKIPPED {stage}")
    if errors:
        print("\nVERIFY FAILED:\n  " + "\n  ".join(errors), file=sys.stderr)
        return 1
    print("\ncloud verify ok" + (f" ({len(skipped)} stage(s) skipped)" if skipped else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
