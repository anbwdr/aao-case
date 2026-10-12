# How I'd productionise it

- **Infrastructure as code:** Terraform for everything in `infra/setup.sh`, state in GCS, one workspace per environment (dev/prod projects).
- **Deploys from CI:** on merge to `main`, deploy the Workflow and the publish job; Dataform already releases from `main`. PRs run `make verify` against a dev project with a sample of the data.
- **Source:** CDC from the till DB (Datastream → BigQuery) instead of nightly CSVs; raw stays append-only, base unchanged.
- **Serving:** Tableau Server/Cloud on BigQuery directly; drop the Sheet bridge. Malloy Publisher on Cloud Run behind IAP.
- **Data quality:** `dq_bets_after_actual_match` becomes an alert to Trading/Risk (past-posting), not a dashboard table.
- **Correlated markets:** fixture-level outcome scenarios for worst/best case once a fixture has more than one market type (PLAN.md §10).
- **Secrets:** the GitHub token moves to a GitHub App installation for Dataform.
- **Cost guards:** `maximumBytesBilled` on Malloy's connection, partition filters on `fct_bets`.
