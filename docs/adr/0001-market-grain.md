# 1. Market is the grain, never fixture

**Context.** Today every fixture has exactly one market (3-way), so fixture and market grain give the same numbers. T6 asks what happens with more markets per fixture.

**Decision.** Every fact and KPI is at market grain (`fct_market`, `fct_market_outcome`). Fixture views aggregate markets.

**Consequences.** Stakes, settled earnings, margin and open bets stay additive for any number of markets: the O/U 2.5 batch adds 380 markets and leaves the graded KPIs unchanged (checked by `make local-full`). Worst/best case is the exception: summing per-market extremes assumes markets are independent, which breaks for correlated markets on the same fixture (PLAN.md §10).
