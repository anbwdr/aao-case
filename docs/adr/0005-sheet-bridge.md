# 5. Google Sheet as the bridge to Tableau Public

**Context.** Tableau Public cannot connect to BigQuery. It can read Google Sheets and keep them in sync.

**Decision.** A Cloud Run job writes each reporting table to a tab of a Sheet owned by a person and shared with the job's service account (service accounts cannot own Drive files). Strings are written as text (option "1" must not become a number next to "X"); dates as real dates. `make verify` reads the Sheet back as that service account and diffs it cell by cell against BigQuery.

**Consequences.** About 2.5M cells, within the 10M limit. In production Tableau Server/Cloud would read BigQuery directly and the job would go.
