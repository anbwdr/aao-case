#!/usr/bin/env bash
# Guided human-only setup (PLAN.md §9 step 4). Resumable: every step checks its own
# state first and is skipped when already done, so just re-run it after a break.
#
#   infra/wizard.sh           run the steps that are not done yet
#   infra/wizard.sh --status  print the checklist only, change nothing
#
# Answers are saved in infra/config.env (gitignored), which infra/setup.sh reads.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="$ROOT/infra/config.env"
STATUS_ONLY=false
[[ "${1:-}" == "--status" ]] && STATUS_ONLY=true

# --- helpers ---------------------------------------------------------------

bold() { printf '\n\033[1m%s\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }
ok() { printf '  \033[32m✓\033[0m %s\n' "$*"; }
todo() { printf '  \033[33m○\033[0m %s\n' "$*"; }
fail() { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

ask() { # ask VAR "question" [default]
  local var=$1 question=$2 default=${3:-} answer
  read -r -p "  $question${default:+ [$default]}: " answer
  printf -v "$var" '%s' "${answer:-$default}"
}

confirm() { # confirm "question" -> 0 on yes
  local answer
  read -r -p "  $1 [y/N]: " answer
  [[ "$answer" =~ ^[Yy]$ ]]
}

pause() { read -r -p "  $1 Press Enter when done. " _; }

from_clipboard() { # from_clipboard VAR: fill an empty answer from the clipboard (terminal paste can fail)
  [[ -n "${!1}" ]] && return
  local clip
  clip=$(wl-paste --no-newline 2>/dev/null || xclip -selection clipboard -o 2>/dev/null || true)
  printf -v "$1" '%s' "$(tr -d '[:space:]' <<<"$clip")"
}

save() { # save KEY VALUE: upsert into config.env
  touch "$CONFIG"
  if grep -q "^$1=" "$CONFIG"; then
    sed -i "s|^$1=.*|$1=$2|" "$CONFIG"
  else
    echo "$1=$2" >>"$CONFIG"
  fi
  printf -v "$1" '%s' "$2"
}

has() { command -v "$1" >/dev/null 2>&1; }

# A step: name, check function, run function. Status mode only reports.
step() {
  local name=$1 check=$2 run=$3
  if $check; then
    ok "$name"
  elif $STATUS_ONLY; then
    todo "$name"
  else
    bold "→ $name"
    $run
    $check || fail "$name is still not done. Fix it and re-run the wizard."
    ok "$name"
  fi
}

# shellcheck source=/dev/null
[[ -f "$CONFIG" ]] && source "$CONFIG"
: "${REGION:=europe-west1}" "${BQ_LOCATION:=EU}"
: "${PUBLISH_SA:=aao-publish}" "${PIPELINE_SA:=aao-pipeline}"
: "${GITHUB_SECRET:=dataform-github-token}"

# --- 1. gcloud --------------------------------------------------------------

check_gcloud() { has gcloud && has bq; }
run_gcloud() {
  info "The Google Cloud CLI is in the AUR as google-cloud-cli; bq is a separate package, google-cloud-cli-bq."
  if confirm "Install them now with yay?"; then
    yay -S --needed google-cloud-cli google-cloud-cli-bq
  else
    pause "Install gcloud and bq yourself."
  fi
  hash -r
}

check_login() { [[ -n "$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null)" ]]; }
run_login() { gcloud auth login; }

check_adc() { gcloud auth application-default print-access-token >/dev/null 2>&1; }
run_adc() {
  info "Application Default Credentials let local tools (Python, Dataform CLI) call GCP as you."
  gcloud auth application-default login
}

# --- 2. GCP project and billing --------------------------------------------

check_project() { [[ -n "${PROJECT_ID:-}" ]] && gcloud projects describe "$PROJECT_ID" >/dev/null 2>&1; }
run_project() {
  info "If you have no GCP account yet, start the free trial (\$300 credit) first:"
  info "  https://console.cloud.google.com/freetrial"
  pause "Make sure the free trial is active."
  ask PROJECT_ID "Project id (6-30 chars, lowercase, globally unique)" "aao-case-$(date +%m%d)"
  if ! gcloud projects describe "$PROJECT_ID" >/dev/null 2>&1; then
    confirm "Create project $PROJECT_ID?" && gcloud projects create "$PROJECT_ID" --name="AAO case"
  fi
  save PROJECT_ID "$PROJECT_ID"
}

check_billing() {
  [[ -n "${PROJECT_ID:-}" ]] &&
    [[ "$(gcloud billing projects describe "$PROJECT_ID" --format='value(billingEnabled)' 2>/dev/null)" == "True" ]]
}
run_billing() {
  local accounts account
  accounts=$(gcloud billing accounts list --filter=open=true --format='value(name,displayName)' 2>/dev/null || true)
  if [[ -z "$accounts" ]]; then
    info "No open billing account found. The free trial creates one."
    pause "Open https://console.cloud.google.com/billing and finish the trial signup."
    accounts=$(gcloud billing accounts list --filter=open=true --format='value(name,displayName)')
  fi
  info "Billing accounts:"
  printf '    %s\n' "$accounts"
  ask account "Billing account id to link" "$(head -n1 <<<"$accounts" | cut -f1)"
  gcloud billing projects link "$PROJECT_ID" --billing-account="$account"
}

check_defaults() {
  [[ -n "${PROJECT_ID:-}" ]] &&
    [[ "$(gcloud config get-value project 2>/dev/null)" == "$PROJECT_ID" ]] &&
    grep -q "^defaultProject: $PROJECT_ID$" "$ROOT/workflow_settings.yaml" &&
    grep -q "^REGION=" "$CONFIG" 2>/dev/null
}
run_defaults() {
  gcloud config set project "$PROJECT_ID"
  gcloud auth application-default set-quota-project "$PROJECT_ID"
  ask REGION "Region for Workflows, Cloud Run and the bucket" "$REGION"
  ask BQ_LOCATION "BigQuery location" "$BQ_LOCATION"
  save REGION "$REGION"
  save BQ_LOCATION "$BQ_LOCATION"
  save PUBLISH_SA "$PUBLISH_SA"
  save PIPELINE_SA "$PIPELINE_SA"
  save GITHUB_SECRET "$GITHUB_SECRET"
  sed -i "s|^defaultProject: .*|defaultProject: $PROJECT_ID|; s|^defaultLocation: .*|defaultLocation: $BQ_LOCATION|" \
    "$ROOT/workflow_settings.yaml"
  info "Updated workflow_settings.yaml (project and location)."
}

# --- 3. GitHub ---------------------------------------------------------------

check_gh() { has gh && gh auth status >/dev/null 2>&1; }
run_gh() {
  info "No GitHub account yet? Create one at https://github.com/signup first."
  has gh || yay -S --needed github-cli
  gh auth login --hostname github.com --git-protocol https --web
}

check_repo() { [[ -n "${GITHUB_REPO:-}" ]] && gh repo view "$GITHUB_REPO" >/dev/null 2>&1; }
run_repo() {
  local user name visibility
  user=$(gh api user --jq .login)
  ask name "Repository name" "aao-case"
  ask visibility "Visibility (public/private). The case data may be public" "public"
  [[ "$visibility" == public || "$visibility" == private ]] || fail "Visibility must be public or private."
  if [[ -z "$(git -C "$ROOT" log --oneline -1 2>/dev/null)" ]]; then
    info "The repo has no commit yet. Staged files:"
    git -C "$ROOT" status --short | sed 's/^/    /' | head -20
    confirm "Commit them as the initial version?" || fail "Commit the scaffold, then re-run."
    git -C "$ROOT" commit -q -m "AAO case: pipeline, Dataform models, checks and Malloy model" \
      -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
  fi
  git -C "$ROOT" branch -M main
  if confirm "Create github.com/$user/$name ($visibility) and push main?"; then
    gh repo create "$user/$name" "--$visibility" --source "$ROOT" --remote origin --push
  fi
  save GITHUB_REPO "$user/$name"
}

check_pat() {
  [[ -n "${PROJECT_ID:-}" ]] &&
    gcloud secrets versions describe latest --secret="$GITHUB_SECRET" --project="$PROJECT_ID" >/dev/null 2>&1
}
run_pat() {
  local token
  info "Dataform reads the repo with a fine-grained personal access token:"
  info "  1. https://github.com/settings/personal-access-tokens/new"
  info "  2. Repository access: only ${GITHUB_REPO:-the case repo}"
  info "  3. Permissions: Contents = Read and write (Metadata = Read is added automatically)"
  info "  4. Expiration: past the presentation date"
  read -r -s -p "  Paste the token (hidden), or copy it and just press Enter: " token
  echo
  from_clipboard token
  [[ "$token" == github_pat_* ]] || fail "That does not look like a fine-grained token (github_pat_...)."
  gcloud services enable secretmanager.googleapis.com --project="$PROJECT_ID"
  if ! gcloud secrets describe "$GITHUB_SECRET" --project="$PROJECT_ID" >/dev/null 2>&1; then
    gcloud secrets create "$GITHUB_SECRET" --project="$PROJECT_ID" --replication-policy=automatic
  fi
  printf '%s' "$token" | gcloud secrets versions add "$GITHUB_SECRET" --project="$PROJECT_ID" --data-file=-
  info "Stored. infra/setup.sh grants the Dataform service agent access to it."
}

# --- 4. Google Sheet ---------------------------------------------------------

check_sheet() { [[ -n "${SHEET_ID:-}" ]]; }
run_sheet() {
  local url
  info "Service accounts cannot own Drive files, so the output Sheet lives in your Drive."
  info "  1. Open https://sheets.new (signed in as the account you'll use in Tableau)"
  info "  2. Name it 'AAO reporting'"
  ask url "Paste the Sheet URL (or copy it and just press Enter)"
  from_clipboard url
  SHEET_ID=$(sed -E 's|.*/spreadsheets/d/([A-Za-z0-9_-]+).*|\1|' <<<"$url")
  [[ "$SHEET_ID" =~ ^[A-Za-z0-9_-]{20,}$ ]] || fail "Could not find a Sheet id in that URL."
  save SHEET_ID "$SHEET_ID"
}

publish_sa_email() { echo "$PUBLISH_SA@$PROJECT_ID.iam.gserviceaccount.com"; }

check_sheet_shared() { [[ "${SHEET_SHARED:-}" == "yes" ]]; }
run_sheet_shared() {
  if ! gcloud iam service-accounts describe "$(publish_sa_email)" --project="$PROJECT_ID" >/dev/null 2>&1; then
    info "The publish service account does not exist yet: run 'make setup' (infra/setup.sh),"
    info "then re-run this wizard to finish this step."
    return 0
  fi
  info "Share the Sheet with the publish service account:"
  info "  https://docs.google.com/spreadsheets/d/$SHEET_ID → Share"
  info "  Add $(publish_sa_email) as Editor, untick 'Notify people'."
  confirm "Done?" && save SHEET_SHARED yes
}

# --- 5. Tableau --------------------------------------------------------------

check_tableau() { [[ "${TABLEAU_READY:-}" == "yes" ]]; }
run_tableau() {
  info "On the machine you'll build the dashboard on:"
  info "  1. Create a Tableau Public account: https://public.tableau.com/app/discover"
  info "  2. Install Tableau Public Desktop: https://www.tableau.com/products/public/download"
  info "  3. Sign in to the desktop app with that account"
  confirm "All three done?" && save TABLEAU_READY yes
}

# --- run ----------------------------------------------------------------------

bold "AAO case – setup wizard"

step "gcloud and bq installed" check_gcloud run_gcloud
step "gcloud logged in" check_login run_login
step "Application Default Credentials" check_adc run_adc
step "GCP project${PROJECT_ID:+ ($PROJECT_ID)}" check_project run_project
step "Billing linked" check_billing run_billing
step "Defaults saved (gcloud, infra/config.env, Dataform)" check_defaults run_defaults
step "GitHub CLI logged in" check_gh run_gh
step "GitHub repo${GITHUB_REPO:+ ($GITHUB_REPO)}" check_repo run_repo
step "GitHub token in Secret Manager ($GITHUB_SECRET)" check_pat run_pat
step "Output Google Sheet" check_sheet run_sheet
step "Tableau Public account and desktop app" check_tableau run_tableau

# Needs the service account from infra/setup.sh, so it comes last and may wait for a re-run.
if check_sheet_shared; then
  ok "Sheet shared with the publish service account"
elif $STATUS_ONLY; then
  todo "Sheet shared with the publish service account (after make setup)"
else
  bold "→ Sheet shared with the publish service account"
  run_sheet_shared
  check_sheet_shared && ok "Sheet shared with the publish service account"
fi

if ! $STATUS_ONLY; then
  bold "Next"
  if check_sheet_shared; then
    info "All done. Run: make setup"
  else
    info "Run: make setup, then infra/wizard.sh again to share the Sheet."
  fi
fi
