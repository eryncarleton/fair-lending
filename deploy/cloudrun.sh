#!/usr/bin/env bash
# Deploy the app to Cloud Run. Run from the project folder in Git Bash, with gcloud signed in and
# your project set (gcloud config set project <id>).
#
#   bash deploy/cloudrun.sh            # deploy, using the existing build/hmda.duckdb
#   bash deploy/cloudrun.sh --export   # rebuild build/hmda.duckdb from your local PostgreSQL first
#
# Deploys the files as they are in this folder now (committed or not). The site is open to anyone;
# nothing on it can change data (see data.py), and at most 3 instances run.
# Override: SERVICE=... REGION=... bash deploy/cloudrun.sh
set -euo pipefail

SERVICE="${SERVICE:-fair-lending}"
REGION="${REGION:-us-west1}"
REPO="${REPO:-fair-lending}"
BASE_PATH="${BASE_PATH:-redlining}"   # served at https://ecarleton.com/redlining via deploy/cloudflare-worker.js
# Streamlit refuses live (WebSocket) connections from pages served at another address unless that
# address is listed here: the public site is ecarleton.com, the service itself is *.run.app.
PUBLIC_ORIGIN="${PUBLIC_ORIGIN:-https://ecarleton.com}"
cd "$(dirname "${BASH_SOURCE[0]}")/.."

PROJECT="$(gcloud config get-value project 2>/dev/null)"
[[ -n "$PROJECT" ]] || { echo "Set a project first: gcloud config set project <id>"; exit 1; }
SA="$SERVICE-run@$PROJECT.iam.gserviceaccount.com"

gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com

if [[ "${1:-}" == "--export" || ! -f build/hmda.duckdb ]]; then
    echo "Building build/hmda.duckdb from local PostgreSQL ..."
    docker compose up -d --wait db
    docker compose build app
    docker compose run --rm --no-deps -T app python scripts/export_duckdb.py
fi

# The app runs as its own service account, with no permissions: it needs nothing from Google Cloud.
if ! gcloud iam service-accounts describe "$SA" >/dev/null 2>&1; then
    gcloud iam service-accounts create "$SERVICE-run" --display-name "fair-lending Cloud Run app"
fi

# has_binding <member> <role> <get-iam-policy command words...>: read-only permission check.
has_binding() {
    local member="$1" role="$2"; shift 2
    "$@" --flatten "bindings[].members" --filter "bindings.members:$member" \
        --format "value(bindings.role)" 2>/dev/null | tr -d '\r' | grep -qx "$role"
}

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
    --clear-secrets \
    --set-env-vars "STREAMLIT_SERVER_BASE_URL_PATH=$BASE_PATH,STREAMLIT_SERVER_CORS_ALLOWED_ORIGINS=$PUBLIC_ORIGIN" \
    --memory 2Gi --cpu 1 --concurrency 20 --cpu-boost \
    --min-instances 0 --max-instances 3 \
    --session-affinity --timeout 3600 \
    --allow-unauthenticated

echo
URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format 'value(status.url)')"
echo "Deployed: $URL/$BASE_PATH/   (public address: https://ecarleton.com/$BASE_PATH, via the Cloudflare Worker)"
