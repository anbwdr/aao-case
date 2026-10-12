# 2. Results come from match_results, joined on teams

**Context.** The case's `markets.result_option_id` uses `-999` for "unsettled". `extra.csv` holds the real results, but 108 of 380 fixtures were played on a different date than the case's `starting_time`.

**Decision.** Base derives `result_option_id` from `match_results`, joined on mapped `(home_team, away_team)` (unique within a season), never on date. The case's value is kept as `source_result_option_id` and must agree for every market it settles (assertion, 70/70). `-999` lives in raw only; base uses NULL + `is_settled`.

**Consequences.** One mapping seed (`base_team_name_map`) for every batch; an unmapped name fails assertions and is listed, rows are never dropped. Settling a new market type is one `case` branch in `base_markets` (O/U 2.5: FTHG + FTAG); an unknown type fails loudly instead of staying silently open.
