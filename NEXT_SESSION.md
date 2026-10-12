# Next session: cloud bring-up

Handover written 2026-10-11. **Claude: read this and `CLAUDE.md` first, then guide the user through the steps below one at a time.** Ask them to run commands with the `!` prefix (e.g. `! make wizard`) so the output lands in the conversation. Don't move to the next step until the current one's "done when" holds. Fix failures from the actual error output, not from guesses.

## Where we are

- Everything that runs without GCP is built and `make verify` is green locally (~50 s): oracle, Dataform compile, lint, local DuckDB run of the compiled graph (batch 1 + O/U + scenario), Malloy check. The cloud stages print SKIPPED.
- **Written but never run against GCP:** `infra/setup.sh`, `ingest/workflow.yaml`, `ingest/upload.sh`, `publish/publish.py`, `checks/verify.py` (cloud parts), `malloy/configure.sh` with BigQuery. Expect fixes on first contact. That's normal; this session is for finding them.
- Git: initialised, 77 files staged, **nothing committed**. The wizard makes the first commit (it asks) when it creates the GitHub repo.
- PLAN.md steps done or written: 1, 2, 3, 5, 6, 7, 8, 10, 11, 12, 14, 14b, 16 (docs), CI. Left for the user: 4 (wizard), 9 (KPI review), 13 + 15 (Tableau), 17 (rehearsal).

## Before starting

- `data/bets.csv` is present (it's gitignored).
- Run `! make verify`. It should end with `cloud verify ok (2 stage(s) skipped)`.

## Steps

### 1. Wizard, first pass (👤 ~30–45 min)

`! make wizard`. It resumes, so re-run it after any break or failure. `! infra/wizard.sh --status` prints the checklist.

| Wizard step | Watch for |
|---|---|
| gcloud install | `yay -S google-cloud-cli google-cloud-cli-bq` (AUR; `bq` is a separate package). Re-open the shell if `gcloud` isn't found |
| Free trial + project | The trial signup happens in the browser. Project ids are globally unique: if creation fails, pick another id |
| Billing | Pick the trial billing account |
| Defaults | Writes `infra/config.env` and sets `defaultProject` in `workflow_settings.yaml`. Default region `europe-west1`, BQ `EU` |
| GitHub | `gh auth login --web`. The repo step commits (it asks) and pushes `main`. Check the push included the updated `workflow_settings.yaml` |
| Token | Fine-grained, only the case repo, Contents read/write. Stored in Secret Manager as `dataform-github-token` |
| Sheet | Create it at sheets.new as the Google account Tableau will use, then paste the URL |
| Tableau | Account plus desktop app on the other machine |
| Share Sheet | Comes **after** `make setup`: the wizard says so and stops |

**Done when:** `--status` shows everything ✓ except "Sheet shared".

### 2. `make setup` (🤖 + 👤 ~15 min, mostly waiting)

`! make setup`. It's idempotent: on any failure, fix it and re-run the whole thing.

Likely first-contact issues:
- **IAM propagation:** creating the Eventarc trigger right after granting `pubsub.publisher` to the GCS agent can fail. Wait a minute and re-run.
- **`gcloud beta`** may prompt to install the beta component. Say yes.
- **Dataform service agent missing:** if the secret binding fails with "service account does not exist", the agent wasn't created yet. Re-run `setup.sh` after the repository call, or open Dataform in the console once.
- **`gcloud run jobs deploy --source`:** the first build creates an Artifact Registry repo and uses the default compute SA (granted `roles/run.builder`). If the build fails on permissions, read the Cloud Build log link in the output.
- **Dataform REST calls** (`dataform_api` in setup.sh) print the JSON error body. Check the region is supported and the repo URL is right.

**Done when:** setup prints `Done.` and the console shows the bucket, the 7 datasets, the Workflow, the trigger, the publish job and the Dataform repo with release configs `main` and `scenario`.

### 3. Wizard, second pass: share the Sheet (👤 2 min)

`! make wizard`. Share the Sheet with `aao-publish@<project>.iam.gserviceaccount.com` as editor.

### 4. First pipeline run (🤖 debugging loop)

`! make upload` (batch 1, `--run-now`, waits and prints the result). On failure it prints the Workflow error. Iterate: fix → `! make deploy-workflow` (or `deploy-publish`) → `! make upload`.

The Workflow is the least-tested part. Check in this order:
1. **Manifest read:** `googleapis.storage.v1.objects.get` with `alt: media` should return the parsed JSON map. If it returns bytes or a string, wrap it in `json.decode(...)`.
2. **Loads:** `jobs.insert` load into `raw_staging`, then the append SQL. Raw counts must equal the manifest.
3. **Dataform compile:** a compilation error means the Workflow raises with the error list. Usual cause: `main` not pushed, or the token can't read the repo.
4. **Dataform invoke:** a failing action or assertion makes the invocation FAILED; open the Dataform UI to see which one. BigQuery-only SQL issues the DuckDB run couldn't catch show up here. Candidates to check first:
   - `qualify` without `where`/`group by` (older BigQuery rejected that; the fix is `where true`)
   - `mapped.*` after a `using` join
   - the `window running` clause in `rpt_option_cumulative`
5. **Publish:** the v1 connector `googleapis.run.v1.namespaces.jobs.run` waits for the job. Job logs are in Cloud Run → Jobs → aao-publish.

Re-running `make upload` makes a new batch (new `batch_id`). That's fine: bets merge on `bet_id`, base takes the latest row per key. It also exercises the "re-upload doesn't break base" requirement of step 6.

**Done when:** `make upload` ends SUCCEEDED and the Sheet has tabs `rpt_kpi_totals`, `rpt_market_summary`, `rpt_option_cumulative`, `rpt_crosscheck`, `dq_bets_after_actual_match`, `_meta`.

### 5. `make verify`, fully green (🤖)

`! make verify`. Now nothing should be SKIPPED. The BigQuery diff against golden and the Sheet diff against BigQuery both run.
- **Sheet read fails with a permission error:** the user's token-creator role on `aao-publish` may still be propagating. Wait a few minutes.
- **Type mismatches in the Sheet diff** (e.g. a string vs a number) mean `publish.cell()` and `verify.sheet_value()` disagree. Fix the publisher, not the check.

**Done when:** no SKIPPED, exit 0. **This is the definition of done for steps 6–8 and 11.**

### 6. O/U batch and scenario push on the cloud (🤖, optional this session)

- `! make upload-ou` → `make verify` is still green (graded numbers unchanged, 760 markets).
- `! make scenario` → Publisher (next step) shows season over. Time it end to end for the presentation (PLAN.md §11). `! make scenario-reset` afterwards.

### 7. Malloy Publisher on BigQuery (👤 5 min)

`! make malloy` → http://localhost:4000. Package `aao` should show the same KPI totals as the Sheet. `aao_scenario` only loads after `make scenario`. Decide here (PLAN.md step 12): Malloy does **not** materialise `reporting` (current default). Record the decision in PLAN.md §11.

### 8. KPI review (👤 step 9, ~20 min)

Walk the user through, against PLAN.md §3:
- `definitions/analytics/fct_bets.sqlx` (bet earnings)
- `fct_market_outcome.sqlx` (earnings if realised)
- `fct_market.sqlx` (worst/best, NULL conventions)
- `reporting/rpt_option_cumulative.sqlx` (T2)

Then mark §3 "re-confirmed" in PLAN.md.

## After this session (not for Claude to do)

- Tableau part 1 (step 13): **prove the parameter action + Dynamic Zone Visibility pattern first** in Tableau Public Desktop, then the tiles and T1/T5.
- Tableau part 2 (step 15), rehearsal (step 17).

## Open items carried over

| Item | Where |
|---|---|
| Scenario push end-to-end time | Step 6 above |
| Malloy materialising `reporting` (default: no) | Step 7 above |
| DZV + parameter action in Tableau Public | Tableau part 1 |
| `dq_bets_after_actual_match` count | 0 locally for batch 1 and the scenario; confirm on BigQuery |
