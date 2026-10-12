// Shared constants for all layers. Keep KPI logic out of here: it belongs in SQL.

const SCENARIO = dataform.projectConfig.vars.scenario === "true";

// Dataset for a modelled layer, redirected to <layer>_scenario during a scenario push.
function dataset(layer) {
  return SCENARIO ? `${layer}_scenario` : layer;
}

// Source convention for "market not settled". Exists in raw only; base uses NULL + is_settled.
const UNSETTLED_RESULT_OPTION_ID = -999;

const OPTION_NAMES = ["1", "X", "2", "Over", "Under"];

module.exports = { SCENARIO, dataset, UNSETTLED_RESULT_OPTION_ID, OPTION_NAMES };
