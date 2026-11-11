#!/usr/bin/env bash
# Deploy the app to Cloud Run. Run from the project folder in Git Bash, with gcloud signed in and
# your project set (gcloud config set project <id>), after setting the site password once with
# deploy/set_password.sh.
#
#   bash deploy/cloudrun.sh            # deploy, using the existing build/hmda.duckdb
#   bash deploy/cloudrun.sh --export   # rebuild build/hmda.duckdb from your local PostgreSQL first
#
# Deploys the files as they are in this folder now (committed or not). The site is open to the
# internet; the app's own sign-in screen asks for the password before showing anything.
# Override: SERVICE=... REGION=... bash deploy/cloudrun.sh
set -euo pipefail

SERVICE="${SERVICE:-fair-lending}"
REGION="${REGION:-us-west1}"
REPO="${REPO:-fair-lending}"
SECRET="fair-lending-password-hash"
BASE_PATH="${BASE_PATH:-redlining}"   # served at https://ecarleton.com/redlining via deploy/cloudflare-worker.js
cd "$(dirname "${BASH_SOURCE[0]}")/.."

PROJECT="$(gcloud config get-value project 2>/dev/null)"
[[ -n "$PROJECT" ]] || { echo "Set a project first: gcloud config set project <id>"; exit 1; }
SA="$SERVICE-run@$PROJECT.iam.gserviceaccount.com"

gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
    secretmanager.googleapis.com
gcloud secrets describe "$SECRET" >/dev/null 2>&1 \
    || { echo "Set the site password first: bash deploy/set_password.sh"; exit 1; }

if [[ "${1:-}" == "--export" || ! -f build/hmda.duckdb ]]; then
    echo "Building build/hmda.duckdb from local PostgreSQL ..."
    docker compose up -d --wait db
    docker compose build app
    docker compose run --rm --no-deps -T app python scripts/export_duckdb.py
fi

# The app runs as its own service account, whose only permission is reading the password hash.
if ! gcloud iam service-accounts describe "$SA" >/dev/null 2>&1; then
    gcloud iam service-accounts create "$SERVICE-run" --display-name "fair-lending Cloud Run app"
fi

# has_binding <member> <role> <get-iam-policy command words...>: read-only permission check.
has_binding() {
    local member="$1" role="$2"; shift 2
    "$@" --flatten "bindings[].members" --filter "bindings.members:$member" \
        --format "value(bindings.role)" 2>/dev/null | tr -d '\r' | grep -qx "$role"
}

# Lets the app read the password hash (a grant on this one secret only). Done on the first deploy.
if ! has_binding "serviceAccount:$SA" roles/secretmanager.secretAccessor gcloud secrets get-iam-policy "$SECRET"; then
    gcloud secrets add-iam-policy-binding "$SECRET" --member "serviceAccount:$SA" \
        --role roles/secretmanager.secretAccessor --condition None >/dev/null
fi

TAG="$(git rev-parse --short HEAD)$([[ -n "$(git status --porcelain)" ]] && echo -modified)-$(date +%Y%m%d-%H%M%S)"
IMAGE="$REGION-docker.pkg.dev/$PROJECT/$REPO/app:$TAG"
echo "Project $PROJECT, service $SERVICE in $REGION, image tag $TAG"

# New projects run Cloud Build as the Compute Engine default service account without the permissions
# a build needs (reading the uploaded source, pushing the image, writing logs). Google's fix is one
# project-wide role grant; the script only checks for it, and you grant it once yourself.
BUILD_SA="$(gcloud projects describe "$PROJECT" --format 'value(projectNumber)')-compute@developer.gserviceaccount.com"
if ! has_binding "serviceAccount:$BUILD_SA" roles/cloudbuild.builds.builder gcloud projects get-iam-policy "$PROJECT"; then
    echo "Cloud Build needs one permission first. Run this once, then deploy again:"
    echo "  gcloud projects add-iam-policy-binding $PROJECT --member serviceAccount:$BUILD_SA --role roles/cloudbuild.builds.builder --condition None"
    exit 1
fi

gcloud artifacts repositories describe "$REPO" --location "$REGION" >/dev/null 2>&1 \
    || gcloud artifacts repositories create "$REPO" --repository-format docker --location "$REGION"

gcloud builds submit --config deploy/cloudbuild.yaml --substitutions "_IMAGE=$IMAGE" .

# Streamlit keeps a live connection per browser tab: session affinity keeps a tab on one instance,
# and the long timeout lets a session stay open for an hour. At most 3 instances caps the cost.
gcloud run deploy "$SERVICE" --image "$IMAGE" --region "$REGION" \
    --service-account "$SA" \
    --set-secrets "FAIR_LENDING_PASSWORD_HASH=$SECRET:latest" \
    --set-env-vars "STREAMLIT_SERVER_BASE_URL_PATH=$BASE_PATH" \
    --memory 2Gi --cpu 1 --concurrency 20 --cpu-boost \
    --min-instances 0 --max-instances 3 \
    --session-affinity --timeout 3600 \
    --allow-unauthenticated

echo
URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format 'value(status.url)')"
echo "Deployed: $URL/$BASE_PATH/   (public address: https://ecarleton.com/$BASE_PATH, via the Cloudflare Worker)"
