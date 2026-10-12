"""Cloud Run job: copy reporting.* from BigQuery into the output Google Sheet, one tab per table.

Tableau Public cannot reach BigQuery; it reads this Sheet ("keep data in sync"). The Sheet is
owned by a person and shared with this job's service account as editor (service accounts
cannot own Drive files).

Env: PROJECT_ID, SHEET_ID, REPORTING_DATASET (default reporting).
Run locally with ADC that can impersonate the publish SA, or as yourself if the Sheet is yours:
  PROJECT_ID=... SHEET_ID=... python publish/publish.py
"""

from __future__ import annotations

import datetime as dt
import decimal
import logging
import os
import sys

import google.auth
import gspread
from google.cloud import bigquery

# Tab name = table name. Order = tab order in the Sheet.
TABLES = {
    "rpt_kpi_totals": "1",
    "rpt_market_summary": "fixture_start_date, market_id",
    "rpt_option_cumulative": "market_id, option_id, bet_date",
    "rpt_crosscheck": "market_id",
    "dq_bets_after_actual_match": "fixture_id, bet_date",
}
META_TAB = "_meta"
CHUNK_ROWS = 20_000  # keeps each Sheets API request well under the payload limit
SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/bigquery"]

log = logging.getLogger("publish")


def cell(value: object) -> object:
    """BigQuery value -> JSON value the Sheets API accepts. Dates become ISO strings, which
    USER_ENTERED parses into real date cells. Strings get a leading apostrophe so USER_ENTERED
    keeps them as text: option names "1" and "2" must not turn into numbers next to "X"."""
    if value is None:
        return ""
    if isinstance(value, str):
        return "'" + value
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    return value


def read_table(client: bigquery.Client, dataset: str, table: str, order_by: str) -> tuple[list[str], list[list]]:
    result = client.query(f"select * from `{client.project}.{dataset}.{table}` order by {order_by}").result()
    header = [field.name for field in result.schema]
    return header, [[cell(v) for v in row.values()] for row in result]


def write_tab(sheet: gspread.Spreadsheet, title: str, header: list[str], rows: list[list]) -> None:
    n_rows, n_cols = len(rows) + 1, len(header)
    try:
        tab = sheet.worksheet(title)
    except gspread.WorksheetNotFound:
        tab = sheet.add_worksheet(title, rows=n_rows, cols=n_cols)
    tab.clear()
    tab.resize(rows=n_rows, cols=n_cols)
    values = [[cell(h) for h in header], *rows]
    for start in range(0, len(values), CHUNK_ROWS):
        chunk = values[start : start + CHUNK_ROWS]
        tab.update(chunk, f"A{start + 1}", value_input_option="USER_ENTERED")
    tab.freeze(rows=1)
    log.info("%s: %d rows", title, len(rows))


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    project = os.environ["PROJECT_ID"]
    sheet_id = os.environ["SHEET_ID"]
    dataset = os.environ.get("REPORTING_DATASET", "reporting")

    credentials, _ = google.auth.default(scopes=SCOPES)
    bq = bigquery.Client(project=project, credentials=credentials)
    sheet = gspread.authorize(credentials).open_by_key(sheet_id)

    counts = {}
    for table, order_by in TABLES.items():
        header, rows = read_table(bq, dataset, table, order_by)
        write_tab(sheet, table, header, rows)
        counts[table] = len(rows)

    as_of = bq.query(f"select as_of_date from `{project}.{dataset}.rpt_kpi_totals`").result()
    meta = {
        "published_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "as_of_date": next(iter(as_of))[0],
        "source": f"{project}.{dataset}",
        **{f"rows.{t}": n for t, n in counts.items()},
    }
    write_tab(sheet, META_TAB, ["key", "value"], [[cell(k), cell(v)] for k, v in meta.items()])

    # A new Sheet starts with an empty "Sheet1": drop it once the real tabs exist.
    for tab in sheet.worksheets():
        if tab.title not in {*TABLES, META_TAB} and not tab.get_all_values():
            sheet.del_worksheet(tab)
    log.info("published %d tabs to %s", len(counts) + 1, sheet_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
