# 4. An independent oracle and a local run of the compiled graph

**Decision.** `checks/oracle.py` computes every KPI from the CSVs in DuckDB, sharing no code with Dataform, and writes `checks/golden/`. `checks/local_dataform.py` compiles the Dataform project, transpiles it to DuckDB (sqlglot), runs it over the real batches and diffs the reporting tables against golden. Once the cloud exists, `checks/verify.py` diffs BigQuery and the Sheet too.

**Consequences.** The SQL is tested before any GCP exists and on every change, in about 30 s. Two KPI implementations must agree to 0.01. It already caught two bugs the reference numbers alone would not have (an unsettled O/U market getting a result; a settled market without bets reporting NULL earnings). The local run proves logic, not BigQuery; the BigQuery diff remains the definition of done.
