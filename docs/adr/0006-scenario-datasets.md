# 6. "What if the season were over" lives in separate datasets

**Decision.** `upload.sh --scenario` lands the remaining results into `raw_scenario`. Dataform runs with `vars.scenario = "true"`: `base_match_results` reads `raw ∪ raw_scenario`, and every model writes to `<layer>_scenario`. Publishing is skipped. Malloy Publisher serves the same model over `analytics_scenario` as package `aao_scenario`. `make scenario-reset` drops the datasets.

**Consequences.** The graded datasets, the Sheet and Tableau are never touched. Unioning at the input (not the marts) avoids double-counting the 310 markets that are open in one world and settled in the other. Built-in assertion views share the `assertions` dataset between both modes; they run right after their table, so the overlap is harmless.
