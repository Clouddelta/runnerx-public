#!/usr/bin/env bash
# Run from the repository root. Called by the manually dispatched workflow.
set -euo pipefail

required=(GCP_PROJECT_ID GCP_REGION ARTIFACT_REPOSITORY CLOUD_RUN_SERVICE CLOUD_SQL_CONNECTION_NAME RUNTIME_SERVICE_ACCOUNT DEPLOY_SERVICE_ACCOUNT DB_NAME DB_USER DB_PASSWORD_SECRET DB_PASSWORD_SECRET_VERSION)
for variable in "${required[@]}"; do
  if [[ -z "${!variable:-}" ]]; then
    echo "Missing configuration: $variable" >&2
    exit 1
  fi
done
if [[ ! "$DB_PASSWORD_SECRET_VERSION" =~ ^[1-9][0-9]*$ ]]; then
  echo "DB_PASSWORD_SECRET_VERSION must pin an existing numeric version." >&2
  exit 1
fi

release_id="r${GITHUB_RUN_ID:-$(date -u +%Y%m%d%H%M%S)}-${GITHUB_RUN_ATTEMPT:-1}"
image="${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT_ID}/${ARTIFACT_REPOSITORY}/runnerx:${release_id}"
revision="${CLOUD_RUN_SERVICE}-${release_id}"
temporary_dir="$(mktemp -d)"
trap 'rm -rf -- "$temporary_dir"' EXIT

# JSON is also valid YAML; serializing avoids unsafe comma-delimited env values.
python - "$temporary_dir/environment.yaml" <<'PY'
import json
import os
import sys

configuration = {
    "APP_ENV": "production",
    "API_DOCS_ENABLED": "false",
    "DB_HOST": "127.0.0.1",
    "DB_PORT": "3306",
    "DB_USER": os.environ["DB_USER"],
    "DB_NAME": os.environ["DB_NAME"],
    "DB_UNIX_SOCKET": "/cloudsql/" + os.environ["CLOUD_SQL_CONNECTION_NAME"],
}
with open(sys.argv[1], "w", encoding="utf-8") as output:
    json.dump(configuration, output)
PY

gcloud auth configure-docker "${GCP_REGION}-docker.pkg.dev" --quiet
docker build --tag "$image" .
docker push "$image"

# List failures are fatal; only a successful empty result means first deployment.
existing_service="$(gcloud run services list --project="$GCP_PROJECT_ID" --region="$GCP_REGION" --filter="metadata.name=${CLOUD_RUN_SERVICE}" --format='value(metadata.name)')"
if [[ -n "$existing_service" ]]; then
  gcloud run services describe "$CLOUD_RUN_SERVICE" --project="$GCP_PROJECT_ID" --region="$GCP_REGION" --format=json > "$temporary_dir/previous-service.json"
  python - "$temporary_dir/previous-service.json" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as source:
    service = json.load(source)
traffic = service.get("status", {}).get("traffic", [])
print("Previous traffic:", ",".join(
    f"{item['revisionName']}={item['percent']}" for item in traffic if item.get("percent")
))
PY
fi

# One task, no retries: concurrent migration execution is deliberately prevented.
gcloud run jobs deploy "${CLOUD_RUN_SERVICE}-migrate" \
  --project="$GCP_PROJECT_ID" --region="$GCP_REGION" \
  --image="$image" --service-account="$RUNTIME_SERVICE_ACCOUNT" \
  --set-cloudsql-instances="$CLOUD_SQL_CONNECTION_NAME" \
  --env-vars-file="$temporary_dir/environment.yaml" \
  --set-secrets="DB_PASSWORD=${DB_PASSWORD_SECRET}:${DB_PASSWORD_SECRET_VERSION}" \
  --command=alembic --args=upgrade,head \
  --tasks=1 --parallelism=1 --max-retries=0 --task-timeout=600s \
  --cpu=1 --memory=512Mi --execute-now --wait --quiet

traffic_flags=()
if [[ -n "$existing_service" ]]; then
  traffic_flags+=(--no-traffic)
else
  echo "Initial deployment: the first revision is immediately available to authorized IAM callers."
fi

gcloud run deploy "$CLOUD_RUN_SERVICE" \
  --project="$GCP_PROJECT_ID" --region="$GCP_REGION" \
  --image="$image" --service-account="$RUNTIME_SERVICE_ACCOUNT" \
  --set-cloudsql-instances="$CLOUD_SQL_CONNECTION_NAME" \
  --env-vars-file="$temporary_dir/environment.yaml" \
  --set-secrets="DB_PASSWORD=${DB_PASSWORD_SECRET}:${DB_PASSWORD_SECRET_VERSION}" \
  --port=8000 --cpu=1 --memory=512Mi --concurrency=20 \
  --min=0 --max=3 --timeout=60s \
  --no-allow-unauthenticated --invoker-iam-check \
  --execution-environment=gen2 \
  --revision-suffix="$release_id" --tag=candidate \
  "${traffic_flags[@]}" --quiet

gcloud run services describe "$CLOUD_RUN_SERVICE" \
  --project="$GCP_PROJECT_ID" --region="$GCP_REGION" --format=json > "$temporary_dir/candidate-service.json"
service_url="$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["status"]["url"])' "$temporary_dir/candidate-service.json")"
candidate_url="$(python -c 'import json,sys; print(next(t["url"] for t in json.load(open(sys.argv[1]))["status"]["traffic"] if t.get("tag") == "candidate"))' "$temporary_dir/candidate-service.json")"

# Token audience is the service URL, even when requesting its tagged URL.
identity_token="$(gcloud auth print-identity-token --impersonate-service-account="$DEPLOY_SERVICE_ACCOUNT" --audiences="$service_url" --include-email)"
if [[ -n "${GITHUB_ACTIONS:-}" ]]; then
  echo "::add-mask::$identity_token"
fi
curl --fail --silent --show-error --retry 5 --retry-delay 3 --retry-all-errors \
  --connect-timeout 10 --max-time 30 \
  -H "Authorization: Bearer $identity_token" "$candidate_url/ready"
unset identity_token

# An unsuccessful migration, deployment or readiness request exits before this.
gcloud run services update-traffic "$CLOUD_RUN_SERVICE" \
  --project="$GCP_PROJECT_ID" --region="$GCP_REGION" \
  --to-revisions="${revision}=100" --quiet
echo "Released ${revision}: ${service_url} (IAM authentication required)."
