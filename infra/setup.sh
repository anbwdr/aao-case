#!/usr/bin/env bash
# Idempotent GCP setup (PLAN.md §9 step 5, plus the deploys of steps 6, 10 and 11).
# Safe to re-run: every resource is checked before it is created, IAM bindings are additive.
# In production this would be Terraform.
#
#   infra/setup.sh            everything
#   infra/setup.sh workflow   redeploy ingest/workflow.yaml only
#   infra/setup.sh publish    redeploy the publish Cloud Run job only
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=infra/env.sh
source "$ROOT/infra/env.sh"

log() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
exists() { "$@" >/dev/null 2>&1; }


project_role() { # project_role MEMBER ROLE
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="$1" --role="$2" \
    --condition=None --quiet >/dev/null
}

dataset_role() { # dataset_role DATASET ROLE MEMBER  (BigQuery DCL, idempotent)
  bq --project_id="$PROJECT_ID" --location="$BQ_LOCATION" query --nouse_legacy_sql --quiet \
    "grant \`$2\` on schema \`$PROJECT_ID.$1\` to '$3'" >/dev/null
}

# Dataform has no gcloud surface: call the REST API.
dataform_api() { # dataform_api METHOD PATH [JSON]  -> prints body, fails on HTTP >= 400
  local method=$1 path=$2 body=${3:-} out code
  out=$(mktemp)
  code=$(curl -sS -o "$out" -w '%{http_code}' -X "$method" \
    -H "Authorization: Bearer $(gcloud auth print-access-token)" \
    -H "Content-Type: application/json" \
    ${body:+--data "$body"} \
    "https://dataform.googleapis.com/v1beta1/$path")
  cat "$out"
  rm -f "$out"
  [[ "$code" -lt 400 ]]
}

apis() {
  log "APIs"
  gcloud services enable --project="$PROJECT_ID" \
    bigquery.googleapis.com storage.googleapis.com \
    workflows.googleapis.com workflowexecutions.googleapis.com \
    eventarc.googleapis.com pubsub.googleapis.com \
    run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
    dataform.googleapis.com secretmanager.googleapis.com \
    iam.googleapis.com iamcredentials.googleapis.com \
    sheets.googleapis.com logging.googleapis.com
}

service_accounts() {
  log "Service accounts"
  local sa
  for sa in "$PIPELINE_SA:AAO pipeline (Workflow, Eventarc)" \
    "$DATAFORM_SA:AAO Dataform runs" \
    "$PUBLISH_SA:AAO publish job (BigQuery -> Sheet)"; do
    local name=${sa%%:*} display=${sa#*:}
    exists gcloud iam service-accounts describe "$name@$PROJECT_ID.iam.gserviceaccount.com" --project="$PROJECT_ID" ||
      gcloud iam service-accounts create "$name" --project="$PROJECT_ID" --display-name="$display"
  done
}

storage() {
  log "Landing bucket gs://$BUCKET"
  exists gcloud storage buckets describe "gs://$BUCKET" ||
    gcloud storage buckets create "gs://$BUCKET" --project="$PROJECT_ID" --location="$REGION" \
      --uniform-bucket-level-access --public-access-prevention
}

datasets() {
  log "BigQuery datasets ($BQ_LOCATION)"
  local ds
  for ds in "${RAW_DATASETS[@]}" "${MODEL_DATASETS[@]}"; do
    exists bq --project_id="$PROJECT_ID" show --dataset "$PROJECT_ID:$ds" ||
      bq --project_id="$PROJECT_ID" --location="$BQ_LOCATION" mk --dataset "$PROJECT_ID:$ds"
  done
  # Staging tables are dropped after each load; expiry is the safety net.
  bq --project_id="$PROJECT_ID" update --default_table_expiration 86400 "$PROJECT_ID:raw_staging" >/dev/null
  for ds in raw raw_scenario; do
    bq --project_id="$PROJECT_ID" --location="$BQ_LOCATION" query --nouse_legacy_sql --quiet \
      "create table if not exists \`$PROJECT_ID.$ds.batch_manifest\` (
         batch_id string not null, table_name string not null, row_count int64 not null,
         source_uri string, loaded_at timestamp)
       options (description = 'One row per landed file, written by the Workflow after the row counts matched.')" >/dev/null
  done
}

iam() {
  log "IAM (least privilege)"
  local pipeline="serviceAccount:$PIPELINE_SA_EMAIL"
  local dataform="serviceAccount:$DATAFORM_SA_EMAIL"
  local publish="serviceAccount:$PUBLISH_SA_EMAIL"
  local number dataform_agent gcs_agent build_sa me
  number=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
  dataform_agent="serviceAccount:service-$number@gcp-sa-dataform.iam.gserviceaccount.com"
  gcs_agent="serviceAccount:$(gcloud storage service-agent --project="$PROJECT_ID")"
  build_sa="serviceAccount:$number-compute@developer.gserviceaccount.com"
  me="user:$(gcloud config get-value account 2>/dev/null)"

  # Pipeline: read landed files, load raw, start Dataform, run the publish job, receive events.
  gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" --member="$pipeline" \
    --role=roles/storage.objectViewer >/dev/null
  for role in roles/bigquery.jobUser roles/dataform.editor roles/run.invoker roles/run.viewer \
    roles/eventarc.eventReceiver roles/workflows.invoker roles/logging.logWriter; do
    project_role "$pipeline" "$role"
  done
  for ds in "${RAW_DATASETS[@]}"; do dataset_role "$ds" roles/bigquery.dataEditor "$pipeline"; done
  gcloud iam service-accounts add-iam-policy-binding "$DATAFORM_SA_EMAIL" --project="$PROJECT_ID" \
    --member="$pipeline" --role=roles/iam.serviceAccountUser >/dev/null

  # Dataform: builds base/analytics/reporting (and their _scenario copies), reads raw.
  project_role "$dataform" roles/bigquery.jobUser
  project_role "$dataform" roles/bigquery.dataEditor
  # Dataform service agent: read the GitHub token, act as the Dataform SA.
  gcloud beta services identity create --service=dataform.googleapis.com --project="$PROJECT_ID" >/dev/null 2>&1 || true
  gcloud secrets add-iam-policy-binding "$GITHUB_SECRET" --project="$PROJECT_ID" \
    --member="$dataform_agent" --role=roles/secretmanager.secretAccessor >/dev/null
  gcloud iam service-accounts add-iam-policy-binding "$DATAFORM_SA_EMAIL" --project="$PROJECT_ID" \
    --member="$dataform_agent" --role=roles/iam.serviceAccountTokenCreator >/dev/null

  # Publish: read reporting only.
  project_role "$publish" roles/bigquery.jobUser
  dataset_role reporting roles/bigquery.dataViewer "$publish"
  # You: impersonate the publish SA so `make verify` can read the Sheet it writes.
  gcloud iam service-accounts add-iam-policy-binding "$PUBLISH_SA_EMAIL" --project="$PROJECT_ID" \
    --member="$me" --role=roles/iam.serviceAccountTokenCreator >/dev/null

  # Eventarc direct GCS events are published by the Cloud Storage service agent.
  project_role "$gcs_agent" roles/pubsub.publisher
  # `gcloud run jobs deploy --source` builds with the default compute SA.
  project_role "$build_sa" roles/run.builder
}

dataform_repo() {
  log "Dataform repository ($DATAFORM_REPO_ID, $REGION) -> github.com/${GITHUB_REPO:?GITHUB_REPO missing}"
  local parent="projects/$PROJECT_ID/locations/$REGION"
  local repo_body
  repo_body=$(cat <<EOF
{
  "gitRemoteSettings": {
    "url": "https://github.com/$GITHUB_REPO.git",
    "defaultBranch": "main",
    "authenticationTokenSecretVersion": "projects/$PROJECT_ID/secrets/$GITHUB_SECRET/versions/latest"
  },
  "serviceAccount": "$DATAFORM_SA_EMAIL"
}
EOF
)
  if exists dataform_api GET "$DATAFORM_REPO"; then
    dataform_api PATCH "$DATAFORM_REPO?updateMask=gitRemoteSettings,serviceAccount" "$repo_body" >/dev/null
  else
    dataform_api POST "$parent/repositories?repositoryId=$DATAFORM_REPO_ID" "$repo_body" >/dev/null
  fi

  # Release configs: what the UI and scheduled runs compile. The Workflow compiles main with the
  # same settings, passing vars.scenario.
  local name scenario
  for name in main scenario; do
    scenario=$([[ $name == scenario ]] && echo true || echo false)
    local body
    body=$(cat <<EOF
{
  "gitCommitish": "main",
  "codeCompilationConfig": {
    "defaultDatabase": "$PROJECT_ID",
    "defaultLocation": "$BQ_LOCATION",
    "vars": {"scenario": "$scenario"}
  }
}
EOF
)
    if exists dataform_api GET "$DATAFORM_REPO/releaseConfigs/$name"; then
      dataform_api PATCH "$DATAFORM_REPO/releaseConfigs/$name" "$body" >/dev/null
    else
      dataform_api POST "$DATAFORM_REPO/releaseConfigs?releaseConfigId=$name" "$body" >/dev/null
    fi
  done
}

publish_job() {
  log "Cloud Run job $PUBLISH_JOB"
  : "${SHEET_ID:?SHEET_ID missing: run infra/wizard.sh}"
  gcloud run jobs deploy "$PUBLISH_JOB" --project="$PROJECT_ID" --region="$REGION" --quiet \
    --source="$ROOT/publish" \
    --service-account="$PUBLISH_SA_EMAIL" \
    --set-env-vars="PROJECT_ID=$PROJECT_ID,SHEET_ID=$SHEET_ID,REPORTING_DATASET=reporting" \
    --task-timeout=900 --max-retries=1 --memory=1Gi
}

workflow() {
  log "Workflow $WORKFLOW"
  gcloud workflows deploy "$WORKFLOW" --project="$PROJECT_ID" --location="$REGION" \
    --source="$ROOT/ingest/workflow.yaml" \
    --service-account="$PIPELINE_SA_EMAIL" \
    --set-env-vars="REGION=$REGION,BQ_LOCATION=$BQ_LOCATION,DATAFORM_REPO=$DATAFORM_REPO,DATAFORM_SA=$DATAFORM_SA_EMAIL,PUBLISH_JOB=$PUBLISH_JOB"
}

trigger() {
  log "Eventarc trigger $TRIGGER"
  exists gcloud eventarc triggers describe "$TRIGGER" --project="$PROJECT_ID" --location="$REGION" ||
    gcloud eventarc triggers create "$TRIGGER" --project="$PROJECT_ID" --location="$REGION" \
      --destination-workflow="$WORKFLOW" --destination-workflow-location="$REGION" \
      --event-filters="type=google.cloud.storage.object.v1.finalized" \
      --event-filters="bucket=$BUCKET" \
      --service-account="$PIPELINE_SA_EMAIL"
  echo "  The trigger fires on every object; the Workflow ignores everything but _MANIFEST.json."
}

case "${1:-all}" in
  all)
    gcloud config set project "$PROJECT_ID" >/dev/null
    apis
    service_accounts
    storage
    datasets
    iam
    dataform_repo
    publish_job
    workflow
    trigger
    log "Done. Next: share the Sheet (infra/wizard.sh), then make upload"
    ;;
  workflow) workflow ;;
  publish) publish_job ;;
  dataform) dataform_repo ;;
  *) echo "usage: $0 [all|workflow|publish|dataform]" >&2; exit 2 ;;
esac
