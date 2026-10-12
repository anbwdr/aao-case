#!/usr/bin/env bash
# Land one batch in gs://<project>-landing/batch=<id>/ (PLAN.md §4 batch contract).
# Data files first, _MANIFEST.json last: the manifest is what starts the pipeline.
#
#   ingest/upload.sh                  batch 1: case tables + results for the 70 settled fixtures
#   ingest/upload.sh --ou             batch 2: Over/Under 2.5 markets and options (no bets)
#   ingest/upload.sh --scenario       scenario push: remaining results -> raw_scenario (§4.1)
#   ... --run-now                     start the Workflow directly and wait for it (debugging,
#                                     Claude Code loops). Eventarc ignores that manifest.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=infra/env.sh
source "$ROOT/infra/env.sh"

kind=initial
run_now=false
for arg in "$@"; do
  case "$arg" in
    --ou) kind=ou ;;
    --scenario) kind=scenario ;;
    --run-now) run_now=true ;;
    *) echo "usage: $0 [--ou|--scenario] [--run-now]" >&2; exit 2 ;;
  esac
done

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

uv run "$ROOT/ingest/prepare_batch.py" "$kind" "$work"
batch_id=$(jq -r .batch_id "$work/_MANIFEST.json")
dest="gs://$BUCKET/batch=$batch_id"

echo "Uploading to $dest/"
gcloud storage cp "$work"/*.csv "$dest/"

if ! $run_now; then
  gcloud storage cp "$work/_MANIFEST.json" "$dest/_MANIFEST.json"
  echo "Manifest landed: Eventarc starts $WORKFLOW. Follow it with:"
  echo "  gcloud workflows executions list $WORKFLOW --location=$REGION --limit=3"
  exit 0
fi

gcloud storage cp --custom-metadata=trigger=manual "$work/_MANIFEST.json" "$dest/_MANIFEST.json"
event=$(jq -nc --arg bucket "$BUCKET" --arg name "batch=$batch_id/_MANIFEST.json" \
  '{manual: true, data: {bucket: $bucket, name: $name, metadata: {trigger: "manual"}}}')
echo "Running $WORKFLOW and waiting..."
started=$(date +%s)
execution=$(gcloud workflows execute "$WORKFLOW" --location="$REGION" --data="$event" --format='value(name)')
gcloud workflows executions wait "$execution" --location="$REGION" >/dev/null || true
state=$(gcloud workflows executions describe "$execution" --location="$REGION" --format='value(state)')
echo "$WORKFLOW: $state after $(($(date +%s) - started))s"
if [[ "$state" != "SUCCEEDED" ]]; then
  gcloud workflows executions describe "$execution" --location="$REGION" --format='value(error.payload)' >&2
  exit 1
fi
gcloud workflows executions describe "$execution" --location="$REGION" --format='value(result)'
