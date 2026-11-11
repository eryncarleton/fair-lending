#!/usr/bin/env bash
# Set or change the hosted site's password. Run it yourself, from Git Bash in the project folder:
#
#   bash deploy/set_password.sh
#
# It asks for the password twice (nothing shows as you type), hashes it on this computer and stores
# only the hash in Google Secret Manager (secret "fair-lending-password-hash"). If the site is already
# running, it restarts it on the new password; everyone has to sign in again.
set -euo pipefail

SERVICE="${SERVICE:-fair-lending}"
REGION="${REGION:-us-west1}"
SECRET="fair-lending-password-hash"
cd "$(dirname "${BASH_SOURCE[0]}")/.."

PROJECT="$(gcloud config get-value project 2>/dev/null)"
[[ -n "$PROJECT" ]] || { echo "Set a project first: gcloud config set project <id>"; exit 1; }

read -rsp "New site password (at least 12 characters): " PW; echo
read -rsp "Same password again: " PW2; echo
[[ "$PW" == "$PW2" ]] || { echo "The two didn't match. Nothing changed."; exit 1; }
(( ${#PW} >= 12 )) || { echo "Use at least 12 characters (a few random words works well). Nothing changed."; exit 1; }

echo "Hashing ..."
HASH="$(printf '%s' "$PW" | docker compose run --rm --no-deps -T app python scripts/hash_password.py)"
unset PW PW2
[[ "$HASH" == pbkdf2_sha256\$* ]] || { echo "Hashing failed. Nothing changed."; exit 1; }

gcloud services enable secretmanager.googleapis.com
gcloud secrets describe "$SECRET" >/dev/null 2>&1 \
    || gcloud secrets create "$SECRET" --replication-policy automatic
printf '%s' "$HASH" | gcloud secrets versions add "$SECRET" --data-file=-

if gcloud run services describe "$SERVICE" --region "$REGION" >/dev/null 2>&1; then
    echo "Restarting the site on the new password ..."
    gcloud run services update "$SERVICE" --region "$REGION" \
        --update-secrets "FAIR_LENDING_PASSWORD_HASH=$SECRET:latest"
fi
echo "Password saved."
