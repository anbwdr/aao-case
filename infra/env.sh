# Sourced by infra/setup.sh, ingest/upload.sh and the Makefile helpers.
# Reads infra/config.env (written by infra/wizard.sh) and derives every resource name
# in one place, so scripts never spell them differently.
# shellcheck shell=bash disable=SC2034  # names are used by the scripts that source this

_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
_CONFIG="$_ROOT/infra/config.env"
if [[ ! -f "$_CONFIG" ]]; then
  echo "infra/config.env not found: run infra/wizard.sh first." >&2
  exit 1
fi
# shellcheck source=/dev/null
source "$_CONFIG"
: "${PROJECT_ID:?PROJECT_ID missing in infra/config.env}"
: "${REGION:=europe-west1}" "${BQ_LOCATION:=EU}"
: "${PUBLISH_SA:=aao-publish}" "${PIPELINE_SA:=aao-pipeline}" "${DATAFORM_SA:=aao-dataform}"
: "${GITHUB_SECRET:=dataform-github-token}"

BUCKET="$PROJECT_ID-landing"
WORKFLOW="aao-pipeline"
TRIGGER="aao-landing-manifest"
PUBLISH_JOB="aao-publish"
DATAFORM_REPO_ID="aao-case"
DATAFORM_REPO="projects/$PROJECT_ID/locations/$REGION/repositories/$DATAFORM_REPO_ID"

PIPELINE_SA_EMAIL="$PIPELINE_SA@$PROJECT_ID.iam.gserviceaccount.com"
PUBLISH_SA_EMAIL="$PUBLISH_SA@$PROJECT_ID.iam.gserviceaccount.com"
DATAFORM_SA_EMAIL="$DATAFORM_SA@$PROJECT_ID.iam.gserviceaccount.com"

# Graded datasets. The scenario push writes *_scenario copies (PLAN.md §4.1).
RAW_DATASETS=(raw raw_scenario raw_staging)
MODEL_DATASETS=(base analytics reporting assertions)
SCENARIO_DATASETS=(raw_scenario base_scenario analytics_scenario reporting_scenario)

export PROJECT_ID REGION BQ_LOCATION BUCKET WORKFLOW PUBLISH_JOB DATAFORM_REPO SHEET_ID
