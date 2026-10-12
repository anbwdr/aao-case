#!/usr/bin/env bash
# Write malloy/publisher.config.json from infra/config.env and build the scenario package.
# Two packages in one environment:
#   aao           graded datasets (analytics.*)
#   aao_scenario  the same model over analytics_scenario.* (PLAN.md §4.1). It only loads after
#                 `make scenario`; until then Publisher lists it under loadErrors.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=infra/env.sh
source "$ROOT/infra/env.sh"
cd "$ROOT/malloy"

rm -rf .scenario && mkdir -p .scenario/aao_scenario
sed "s/table('analytics\./table('analytics_scenario./" aao/aao.malloy >.scenario/aao_scenario/aao.malloy
jq '.name = "aao_scenario" | .description = "SCENARIO (season over): " + .description' \
  aao/publisher.json >.scenario/aao_scenario/publisher.json

jq -n --arg project "$PROJECT_ID" --arg location "$BQ_LOCATION" '{
  frozenConfig: false,
  environments: [{
    name: "aao",
    packages: [
      {name: "aao", location: "./aao"},
      {name: "aao_scenario", location: "./.scenario/aao_scenario"}
    ],
    connections: [{
      name: "bigquery",
      type: "bigquery",
      bigqueryConnection: {defaultProjectId: $project, billingProjectId: $project, location: $location}
    }]
  }]
}' >publisher.config.json
echo "malloy/publisher.config.json written for $PROJECT_ID ($BQ_LOCATION)"
