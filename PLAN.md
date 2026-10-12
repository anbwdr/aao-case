# AAO Case – Project Plan

Plan for the Etraveli "Against All Odds" BI case. The whole platform is built by Claude Code; the human time is spent on setup, steering, Tableau and the presentation.

- **Human budget:** 6–8 hours over 4 evenings. Machine hours don't count.
- **Definition of done for every Claude Code step:** `make verify` is green (see §6).

---

## 1. What wins the case

The case (`Instruction.docx`) grades:
- understanding the instructions and the data relationships
- KPI definitions
- structuring a BI application
- cross-checks and sanity checks
- coding standards
- explaining the code
- choice of visualisations

The job description adds analytics engineering, dbt/new data platform, pipelines and Finance stakeholders.

**Presentation stance:** lead with correct KPIs and the dashboard. Then spend about 5 minutes on "how this runs as a platform", and close with the more-markets discussion.

### Graded tasks (must all be demonstrable)

| # | Task | Where it lives |
|---|---|---|
| T1 | [Settled earnings] and [Settled margin]: total, per fixture, and by fixture starting date | Overview page |
| T2 | [Earnings on option if realised]: 3 lines (one per option), x = bet date, y = cumulative earnings. **Only shown when exactly one market is selected** | Market page |
| T3 | T2 works for settled and unsettled markets; the actual outcome is highlighted when settled | Market page |
| T4 | [Settled earnings], [Open bets], [Worst case future earnings] and [Best case future earnings] shown as totals | KPI tiles |
| T5 | Graph of Settled / Best / Worst against fixture starting date | Overview page |
| T6 | Discussion: impact of offering more markets per fixture (e.g. 3-way + Exact result) | Slide + O/U demo |

---

## 2. Data facts (profiled 2026-10-10)

**Bets**
- 1,562,048 rows, from 2023-09-29 to 2024-10-16, about 4,000 per day.
- No nulls, no duplicate ids, no orphans, and no bets placed after kickoff.

**Fixtures, markets and options**
- 380 fixtures for the 2024/25 Premier League season.
- Exactly one market per fixture ("3-way"), each with 3 options (`1`, `X`, `2`).
- 70 markets are settled; the last settled kickoff is 2024-10-06. Open markets start 2024-10-19.
- `result_option_id = -999` means the market is unsettled.

**"Today"**
- `as_of_date` = max bet date = **2024-10-16**. It is derived from the data, never `current_date()`.

**`extra.csv`** (football-data.co.uk, E0, 2024/25 season)
- All 380 fixtures match on (home_team, away_team).
- Team-name mapping needed: `Man United → Man Utd` and `Tottenham → Spurs`.
- **Dates differ for 108 fixtures** (postponements). Join on teams, never on date. The case's `starting_time` stays the official fixture date. None of the 70 settled fixtures are affected.
- Cross-check: all 70 settled `result_option_id` values agree with the actual result (`FTR`).
- `(home_team, away_team)` is unique within a season, so it is a safe join key whatever the dates say. The `extra.csv` date is carried as `actual_match_date` for transparency.

### Reference KPIs (pandas oracle, to be reproduced by `checks/oracle.py`)

| KPI | Value |
|---|---|
| Stake on settled fixtures | 9,524,903 |
| Settled earnings | ≈ 603,336 |
| Settled margin | ≈ 6.33 % |
| Open bets | ≈ 16,105,242 |
| Worst case future earnings | ≈ −862,724 |
| Best case future earnings | ≈ +3,953,924 |

### Size of the option-cumulative series
- About 232k sparse rows, or about 254k when densified.
- That is roughly 1.5M cells, which fits in Google Sheets (10M cell cap).

---

## 3. KPI definitions

Earnings are seen from AAO's side.

| KPI | Definition |
|---|---|
| Bet earnings (settled) | `amount − (odds × amount if option_id = result_option_id else 0)` |
| **Settled earnings** | Σ bet earnings over settled markets |
| **Settled margin** | Settled earnings ÷ Σ amount on settled fixtures |
| **Open bets** | Σ amount on unsettled markets |
| **Earnings on option if realised** (market m, option k, up to date d) | Σ amount in m with bet date ≤ d, minus Σ (odds × amount) on k with bet date ≤ d |
| **Worst case future earnings** | Σ over open markets of min over k of (earnings if k realised, all dates) |
| **Best case future earnings** | Σ over open markets of max over k of (earnings if k realised, all dates) |

**Bug in the existing `explore_bets.malloy` (do not reuse its formula):**
- A losing bet earns `+amount`, not `+odds × amount`.
- A winning bet earns `amount − odds × amount`.
- The margin denominator must be restricted to settled fixtures.

**Why the worst/best sum is valid today:** there is one market per fixture and the fixtures are independent. With correlated markets on the same fixture (T6), the sum is no longer valid. See §9.

**Status:** the definitions above are agreed in principle. Re-confirm them during the KPI SQL review (Step 9).

---

## 4. Architecture

```
upload.sh  ──gcloud storage cp──▶  gs://<proj>-landing/batch=<ts>/{fixtures,markets,options,bets,match_results}.csv
                                   + _MANIFEST.json (written LAST: files + row counts)
                                         │  object.finalized
                                         ▼
                                     Eventarc ──▶ Cloud Workflows "aao-pipeline"
                                                    1. exit unless object == _MANIFEST.json
                                                    2. read manifest
                                                    3. per file: BQ load → tmp table (all STRING)
                                                       → INSERT into raw.<table> + _batch_id, _ingested_at, _source_file
                                                    4. assert RAW row counts == manifest
                                                    5. Dataform: compile + invoke (wait)
                                                    6. Cloud Run job "publish": reporting.* → Google Sheet tabs
                                                       (skipped when the manifest says "scenario": true, see §4.1)
                                                    7. log summary
BigQuery datasets:  raw → base → analytics (marts) → reporting
Malloy:             semantic model over analytics marts, served locally via Malloy Publisher
Tableau Public:     reads the output Google Sheet ("keep data in sync")
```

### Layer contracts

| Layer | What it contains | Materialization |
|---|---|---|
| `raw` | Append-only, exact copy of each landed file. All columns STRING, plus `_batch_id`, `_ingested_at`, `_source_file`. Never edited. | tables (loaded by the Workflow) |
| `base` | Current state of the source, cleaned: typed, snake_case, `fixture_id`-style keys, latest row per key across batches. `result_option_id` is **derived from `match_results`** (FTR, joined on mapped teams); no result yet → NULL plus `is_settled`. `-999` exists in raw only. Asserted (see §6). This is "dbt staging" by another name. | dims as **views**; `base_bets` as an **incremental table on `_batch_id`** |
| `analytics` | Kimball star at **market grain**, never fixture grain: `dim_fixture`, `dim_market`, `dim_option`, `dim_date`, `fct_bets`, `fct_option_daily` (option × bet date stakes and payouts) | tables |
| `reporting` | Presentation-ready tables shaped for Tableau (§7) | tables |

**Talking point:** markets must *not* be append-only incremental, because `result_option_id` changes on settlement. Bets are append-only, so incremental is safe for them.

### 4.1 Scenario push (optional, live in the presentation)

Goal: after the push, Malloy Publisher shows the season as over (all 380 markets settled, open bets 0), while the graded numbers stay untouched.

- `upload.sh --scenario` lands only the remaining `match_results` rows (fixtures after 2024-10-06) with `"scenario": true` in `_MANIFEST.json`.
- The Workflow loads them into a separate `raw_scenario` dataset instead of `raw`.
- Dataform runs with a scenario var: `match_results` = `raw` ∪ `raw_scenario` (union at the input, never at the marts), and writes to `base_scenario`, `analytics_scenario` and `reporting_scenario`.
- Publish is skipped; the Google Sheet and Tableau keep the graded state.
- Malloy Publisher is pointed at the `*_scenario` datasets after the push.
- `as_of_date` stays at the max bet date (2024-10-16). Open bets, worst case and best case go to 0 in the scenario, which is expected.
- O/U 2.5 markets (if loaded) settle from the same rows (FTHG+FTAG).
- **Reset:** drop the `*_scenario` datasets, including `raw_scenario`. The graded datasets are never written to.
- `make verify` checks the graded datasets only.
- **Why not union the marts in Publisher:** the 310 open markets exist in both (open in graded, settled in scenario), so a plain union double counts. An overlay in Malloy would work but moves KPI logic out of Dataform.

---

## 5. Decisions log

| Decision | Choice |
|---|---|
| Cloud | GCP free trial ($300 credit) |
| Source system | CLI upload into a GCS landing bucket ("nightly till-DB export"). No Sheets on the ingestion side. |
| Batch contract | Batch folder per upload; `_MANIFEST.json` written last; only the manifest triggers a run |
| Trigger | Eventarc → Workflow (filters for the manifest). `upload.sh --run-now` calls `gcloud workflows run` and waits, for debugging and Claude Code loops |
| Loading | Done by the Workflow's BigQuery connector. Cloud Run is used only for the publish job |
| RAW transformations | Metadata columns only |
| Transform tool | Dataform, connected to GitHub, release from `main` |
| Base vs analytics | Base = cleaned 3NF; analytics = star schemas |
| Semantic layer | Malloy models over the marts. Publisher runs locally (`npx @malloy-publisher/server`). KPI logic lives once in Dataform marts; Malloy measures are thin sums. Whether Malloy materializes `reporting` is revisited in Step 12 (default: no, Dataform builds it) |
| BI | Tableau Public. It cannot reach BigQuery, so reporting tables are pushed to a Google Sheet owned by the user and shared with the pipeline service account (service accounts can't own Drive files) |
| Data visibility | Publishing the case data publicly is OK |
| Infra as code | No Terraform. One idempotent `infra/setup.sh` (gcloud), plus a `Makefile`. "Would be Terraform in production." |
| CI | Optional and last: a GitHub Action running `dataform compile` on PRs |
| `extra.csv` | Lands as `match_results`. Batch 1 holds only the rows whose **case** `starting_time` is ≤ 2024-10-06 (filter after the team join, never on the `extra.csv` date), asserted to be exactly the 70 settled fixtures. Used for (a) deriving `result_option_id`, (b) the O/U 2.5 market |
| Settlement source | Base derives `result_option_id` from `match_results`. The case's `markets.result_option_id` stays in raw as a cross-check only (must agree 70/70) |
| Join rule | `match_results` → fixtures on mapped `(home_team, away_team)` only, one mapping seed for every batch. Never on date |
| `-999` | Source convention, raw only. Base onward uses NULL plus `is_settled` |
| Scenario push | Optional live demo per §4.1: separate `*_scenario` datasets, union at the input, publish skipped, Publisher reads it, reset by dropping datasets |
| Bets after the real match | Reported, not blocking (§6). The data is synthetic, so a failing assertion would only block the run |
| Second market | O/U 2.5 **structure only, no bets**, landed as batch 2. Synthetic bets only if evening 4 has slack, in a flagged scenario kept out of the graded views |
| Grain | Market everywhere, never fixture |

---

## 6. Feedback loop (definition of done for Claude Code)

1. **`checks/oracle.py`:** a DuckDB reference implementation over `data/*.csv`.
   - It shares no code with Dataform.
   - It writes `checks/golden/*.csv`: KPI totals, KPIs per fixture, and option-cumulative series for a sample of markets (settled and open).
   - It must reproduce the §2 reference numbers.
2. **Dataform assertions:**
   - `raw`: row counts per `_batch_id` equal the manifest.
   - `base`: uniqueness and not-null on keys; relationships bet→option→market→fixture; options in {1, X, 2, Over, Under}; `odds ≥ 1`; `amount > 0`; `result_option_id` belongs to its own market.
   - `analytics`/`reporting`: Σ per-fixture settled earnings = total; worst ≤ best per market; stake reconciles raw → reporting.
   - `match_results` mapping is 1:1: every row maps to exactly one fixture, no fixture has two results, no team name left unmapped. Fails loudly, never drops rows.
   - Batch 1 `match_results` covers exactly the 70 fixtures settled in the case.
   - Cross-check: the derived `result_option_id` matches the case's `markets.result_option_id` for all 70 settled markets (and FTHG+FTAG drives O/U).
3. **`make verify`:**
   - Runs the oracle.
   - Queries BigQuery `reporting.*` and diffs it against the golden files (absolute tolerance 0.01).
   - Reads the published Sheet and diffs it against BigQuery.
   - Non-zero exit on any mismatch.
   - Checks the graded datasets only, never `*_scenario`.
4. **Data-quality report (non-blocking):** `dq_bets_after_actual_match` lists bets with `bet_date > actual_match_date` (fixture, bet date, actual match date, stake). Can only happen for the 108 fixtures with differing dates. Same-day bets are not flagged, as `extra.csv` has dates only. `make verify` prints the count and stake as a warning without failing. Runs whenever results arrive (batch 1 and the scenario push).
   - **Talking point:** in production this is a past-posting alert to Trading/Risk, not a pipeline failure.

**Repo `CLAUDE.md` rule:** never mark a step done unless `make verify` passes. Paste the failing diff into the step notes when it doesn't.

---

## 7. Reporting tables and dashboard spec

### Reporting tables (designed backwards from the visuals)

**`rpt_kpi_totals`** – one row
- `as_of_date`
- `settled_earnings`, `settled_stake`, `settled_margin`
- `open_bets`, `worst_case_future`, `best_case_future`

**`rpt_market_summary`** – one row per market
- Identity: `market_id`, `market_name`, `fixture_id`, `fixture_label` ("Home v Away"), `location`, `fixture_start_date`
- Status: `is_settled`, `result_option_name`
- Money: `total_stake`, `settled_earnings`, `settled_margin`, `open_bets`, `worst_case`, `best_case`

**`rpt_option_cumulative`** – one row per market × option × bet date
- Dense from the first to the last bet date of each market.
- Columns: `market_id`, `option_id`, `option_name`, `bet_date`, `cum_stake_market`, `cum_payout_option`, `cum_earnings_if_realised`, `is_actual_outcome`, `is_settled`

**`rpt_crosscheck`** – one row per settled market
- `market_id`, `aao_result`, `actual_result`, `match_flag`

### Dashboard (Tableau Public, reading the Google Sheet)

**Overview page**
- KPI tiles: Settled earnings · Settled margin · Open bets · Worst case future · Best case future. This covers T4, plus the margin.
- Settled earnings (bars) and margin (line) by fixture start date, daily. This covers T1.
- Three-KPI chart against fixture start date: settled earnings as bars up to `as_of_date`, then the worst–best range as a band (or two lines) after it. This covers T5.
- Fixture table with settled earnings, margin, open bets, worst and best per fixture. This covers T1's per-fixture breakdown.

**Market page**
- Clicking a fixture row fires a **parameter action** that sets `p_market_id`.
- **Dynamic Zone Visibility** on the boolean calc `[p_market_id] <> 0` shows the option-cumulative chart only when exactly one market is selected. Clearing the selection resets the parameter.
- 3 lines (1/X/2). When settled, the actual outcome is a thicker line in an accent colour with a "Result" label. This covers T2 and T3.
- Do **not** use a single-value dropdown: it makes "only when one is selected" trivial and misses the point of the task.
- *Verify in evening 3, before building around it:* DZV with a parameter action works in Tableau Public Desktop.

---

## 8. Repo layout

```
aao-case/
├── CLAUDE.md                 # conventions, layer contracts, DoD = make verify
├── README.md                 # architecture diagram, how to run, decisions
├── Makefile                  # setup, upload, run, verify, publish, malloy
├── data/                     # source CSVs (bets.csv via Git LFS or .gitignore + note)
├── infra/
│   ├── setup.sh              # idempotent gcloud: APIs, buckets, datasets, SAs, IAM, Eventarc, Workflow, Cloud Run job
│   └── wizard.sh             # human-only steps, guided
├── ingest/
│   ├── upload.sh             # batch folder + _MANIFEST.json last; --run-now
│   ├── make_ou_batch.py      # builds O/U 2.5 markets/options CSVs from extra.csv (batch 2)
│   └── workflow.yaml         # Cloud Workflows definition
├── dataform/
│   ├── workflow_settings.yaml   # vars: as_of_date strategy, dataset names
│   ├── definitions/{raw_sources,base,analytics,reporting}/
│   └── includes/
├── publish/                  # Cloud Run job: reporting.* → Google Sheet (Python, gspread)
├── malloy/                   # aao.malloy semantic model + views; publisher.config.json
├── checks/                   # oracle.py, golden/, verify.py
└── docs/                     # ADRs (short), presentation notes, screenshots
```

---

## 9. Step-by-step plan

**Legend:** 👤 = human time, 🤖 = Claude Code (machine time, runs between evenings too). Every 🤖 step ends with `make verify` green, unless it's marked *pre-pipeline*.

### Evening 1 – Foundations (👤 ~1.5 h)

1. 🤖 **Repo scaffold and `CLAUDE.md`** (*pre-pipeline*): layout from §8, conventions (SQLFluff/BigQuery style, snake_case, layer contracts), and this plan copied to `docs/PLAN.md`.
2. 🤖 **Oracle** (*pre-pipeline*): write `checks/oracle.py` and the golden files.
   - Done when it reproduces the §2 reference numbers and the cross-check shows 70/70 matches.
3. 🤖 **`infra/wizard.sh`**: the human-only steps, guided.
4. 👤 **Run the wizard:**
   - GCP free trial and project, with billing on
   - Install `gcloud` on omarchy, then `gcloud auth login` and `gcloud auth application-default login`
   - Create a GitHub account and the repo; push the scaffold
   - Create a GitHub fine-grained PAT and store it in Secret Manager for Dataform
   - Create the output Google Sheet in your own Drive and share it with the publish service account as editor
   - Create a Tableau Public account and install Tableau Public Desktop on the other machine
5. 🤖 **`infra/setup.sh`:** enable APIs; create the landing bucket and `raw`/`base`/`analytics`/`reporting` datasets; create service accounts and least-privilege IAM (including the GCS service agent → Pub/Sub publisher for Eventarc). Idempotent, so it can be re-run safely.

### Evening 2 – Pipeline end to end (👤 ~45 min review)

6. 🤖 **Ingestion:**
   - `upload.sh` (batch 1 = the four case tables, plus `match_results` for the 70 fixtures whose case `starting_time` ≤ 2024-10-06, filtered after the team join)
   - `workflow.yaml` steps 1–4, and the Eventarc trigger
   - Done when RAW row counts equal the manifest, and re-uploading a batch appends without breaking base.
7. 🤖 **Dataform `base`:** typed, deduplicated, asserted. `base_bets` incremental. Team-name mapping seed. `result_option_id` derived from `match_results`, `actual_match_date` carried, 1:1 mapping assertion, `dq_bets_after_actual_match` report.
8. 🤖 **Dataform `analytics` and `reporting`:** §4 marts and §7 reporting tables.
   - Workflow step 5 invokes Dataform.
   - Done when `make verify` is green against the oracle.
9. 👤 **Review the KPI SQL against §3.** Re-confirm the definitions here.
10. 🤖 **Dataform↔GitHub:** release config from `main`.

### Evening 3 – Publish, semantics, start Tableau (👤 ~2 h)

11. 🤖 **Publish job** (Cloud Run job, Workflow step 6): `reporting.*` → Sheet tabs.
    - `make verify` includes a Sheet-vs-BigQuery diff.
    - Done when one `upload.sh` produces a refreshed Sheet with no manual steps.
12. 🤖 **Malloy:** write `malloy/aao.malloy` over the marts with measures from §3, plus named views mirroring the dashboard. Run Publisher locally.
    - Done when the Malloy query results equal the golden KPIs.
    - **Decide here** whether Malloy should also materialize `reporting` (default: no).
13. 👤 **Tableau, part 1 (~1.5 h):**
    - Connect to the Sheet with "keep data in sync".
    - First prove the DZV + parameter-action pattern.
    - Then build the KPI tiles and the T1/T5 charts.

### Evening 4 – Finish and rehearse (👤 ~3 h)

14. 🤖 **O/U 2.5 batch:** `make_ou_batch.py` → upload as batch 2.
    - Market and option ids must not collide with existing ones.
    - Settled in base from FTHG+FTAG via the same team mapping; unsettled until a result exists.
    - Done when the graded KPIs are unchanged, there are now 760 markets, and assertions are green. This demos incremental loading.
14b. 🤖 **Scenario push (optional, §4.1):** `upload.sh --scenario`, Workflow branch to `raw_scenario`, Dataform scenario var, Publisher switch, `make scenario-reset`.
    - Done when Publisher shows 380 settled markets with open bets 0, the graded `reporting` and the Sheet are unchanged, and `make verify` stays green.
    - Time the push end to end and keep a pre-run scenario ready as fallback.
15. 👤 **Tableau, part 2 (~1.5 h):** build the market page (T2, T3) and the fixture table; add a "market type" filter defaulting to 3-way; publish to Tableau Public.
16. 🤖 **Docs:** README architecture diagram, ADRs, screenshots, and a "how I'd productionise it" list (Terraform, CI deploys, Publisher on Cloud Run behind IAP, CDC from the till DB).
17. 👤 **Presentation and rehearsal (~1.5 h).** Order:
    1. Business framing and KPI definitions
    2. Dashboard walkthrough
    3. Cross-checks (oracle, assertions, results vs actual results)
    4. Code tour (one model per layer)
    5. Platform in 5 minutes (live scenario push: season over in Publisher, then reset; pre-run fallback ready)
    6. More-markets discussion

**Cut order if time runs short:** scenario push → Malloy Publisher polish → O/U market → CI. Never cut the dashboard or the rehearsal.

---

## 10. T6 talking points: more markets per fixture

- **What already works:** the model is at market grain, so stakes, settled earnings, margin and open bets sum correctly across any number of markets. The O/U batch proves it, since the graded KPIs are unchanged.
- **What breaks:** worst/best case. Outcomes of markets on the same fixture are correlated (e.g. a 2-1 exact score implies a home win and Over 2.5). Summing per-market minimums overstates the worst case, because those minimums can't all happen together.
- **Fix:**
  - Build a fixture-level **outcome scenario** table (e.g. scorelines up to N goals, plus "other").
  - Map each option to the scenarios in which it wins.
  - Compute earnings per fixture × scenario, then take min/max per fixture, then sum across fixtures.
  - Exact-result markets also need a bounded scoreline set and a rule for "any other score".
- **Dashboard impact:** the market page stays per market. Overview worst/best switches to fixture-level scenarios. Add a market-type filter, and decide whether margin should be reported per market type.

---

## 11. Open items (to settle at the step named)

| Item | When |
|---|---|
| Re-confirm KPI definitions (§3) | Step 9 |
| Malloy materializing `reporting`, or not | Step 12 |
| DZV + parameter action works in Tableau Public | Step 13, first thing |
| `bets.csv` (49 MB) in GitHub: Git LFS or keep out of the repo and document | Step 1 |
| Count of bets after the real match (`dq_bets_after_actual_match`) | Step 7 |
| End-to-end time of the scenario push | Step 14b, rehearsal |
