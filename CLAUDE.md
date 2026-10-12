# AAO case – working rules

`PLAN.md` is the source of truth for scope, KPI definitions (§3), architecture (§4) and step order (§9). Read it before starting a step.

If `NEXT_SESSION.md` exists, it is the handover from the previous session: start there.

## Definition of done

- A step is done only when `make verify` is green. Never mark a step done otherwise; paste the failing diff into the step notes instead.
- `make verify` = oracle, Dataform compile (graded + scenario), lint, `local-full` (compiled graph on DuckDB over batch 1 + O/U + scenario, all assertions, diff vs golden), `malloy-check`, `verify-cloud` (BigQuery vs golden, Sheet vs BigQuery). Cloud stages print SKIPPED until set up: a step that needs them is not done while they skip.
- `checks/oracle.py` shares no code with Dataform. Never "fix" the oracle to match Dataform; work out which one is wrong against PLAN.md §3.
- Can't reach GCP from here? Everything except `verify-cloud` still runs locally. Don't claim cloud behaviour you haven't seen.

## Data rules

- `as_of_date` = max bet date (2024-10-16). Never `current_date()`.
- Match results join fixtures on mapped `(home_team, away_team)`, never on date (108 fixtures were rescheduled). Mapping lives in `base_team_name_map` only.
- `-999` exists in raw only. Base onward: `result_option_id` NULL + `is_settled`.
- The result is derived from `match_results`. The source's `result_option_id` is a cross-check (`source_result_option_id`).
- Grain is market, never fixture.
- Money is NUMERIC in BigQuery so sums are exact.
- In reporting, amounts that don't apply to a market's status are NULL, not 0 (e.g. `worst_case` on a settled market).

## Layers (Dataform, `definitions/`)

The Dataform project (`workflow_settings.yaml`, `definitions/`, `includes/`) stays at the repo root: Dataform compiles from the root of the connected git repo and has no subfolder setting.

| Folder | Dataset | Contract |
|---|---|---|
| `raw_sources/` | `raw`, `raw_scenario` | Declarations only. All STRING + `_batch_id`, `_ingested_at`, `_source_file`. Written by the Workflow, never edited |
| `base/` | `base` | Typed, snake_case, latest row per key (`sql.latestPerKey`). Views, except `base_bets` (incremental on `_batch_id`, merged on `bet_id`) and the seed table |
| `analytics/` | `analytics` | Star: `dim_*`, `fct_*`. KPI logic lives here once |
| `reporting/` | `reporting` | Tables shaped for Tableau (PLAN.md §7), plus `dq_*` reports |
| `assertions/` | `assertions` | Cross-table assertions. Single-table checks go in the model's `assertions:` config |

Use `schema: constants.dataset("<layer>")` in every model so the scenario var (`--vars=scenario=true`) redirects to `<layer>_scenario`.

## SQL style

- BigQuery SQL, lowercase keywords, 4-space indent, trailing commas, one column per line in final selects.
- CTEs over subqueries; explicit join type; table aliases are full words (`bets`, `markets`), not letters.
- Every model has a `description` and column descriptions for non-obvious columns.
- Keep SQL portable enough for `checks/local_dataform.py` (sqlglot → DuckDB). E.g. seeds as `select … union all`, not `unnest([struct(…)])`.

## Dataform gotchas (core 3.0.x)

- `partitionBy` / `clusterBy` are top-level config keys, not under `bigqueryOptions`.
- Built-in assertions: `uniqueKeys: [[…], […]]` when a model needs more than one unique key.
- Reference raw tables as `ref("raw", "<table>")`.

## Contracts between parts

- **Batch:** `ingest/prepare_batch.py` writes CSVs + `_MANIFEST.json` (`batch_id`, `kind`, `scenario`, `files[{table, file, columns, rows}]`). The Workflow, `local_dataform.py` and the raw declarations all follow it.
- **`match_results` columns:** `Div, Date, Time, HomeTeam, AwayTeam, FTHG, FTAG, FTR` (the extra.csv subset BigQuery accepts).
- **`raw.batch_manifest` / `raw_scenario.batch_manifest`:** created by `setup.sh`, written by the Workflow after counts match.
- **Market types:** `base_markets` settles `3-way` and `Over/Under 2.5` by exact name. A new type needs a `case` branch there and its option names in `constants.OPTION_NAMES`.
- **Resource names:** only in `infra/env.sh`. Region/project only in `infra/config.env` (gitignored, written by the wizard).
- **Malloy:** measures are thin sums of `fct_market` columns. KPI logic goes in Dataform, never in `aao.malloy`.
- **Sheet:** strings are written as text, dates as dates; `verify.py` is type-strict about it.

## Commands

```
make verify          # definition of done
make oracle          # regenerate checks/golden/
make local-full      # compiled Dataform graph on DuckDB, all batches
make malloy-check    # Malloy model vs golden (after local-full)
make help            # everything, including the cloud targets
```

Python runs through `uv run` (inline script dependencies). Node through `npx` / `npm ci` in `malloy/`.
