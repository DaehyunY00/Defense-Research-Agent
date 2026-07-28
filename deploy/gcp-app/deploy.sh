#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "usage: $0 GCP_PROJECT_ID [REGION]" >&2
  exit 2
fi

GCP_PROJECT_ID=$1
GCP_REGION=${2:-asia-northeast3}
DEPLOY_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd "${DEPLOY_DIR}/../.." && pwd)
REPOSITORY_NAME=defense-research
IMAGE_NAME=defense-research-agent
SECRET_ID=anthropic-api-key
CORPUS_BUCKET="${GCP_PROJECT_ID}-defense-research-corpus"
DEPLOYMENT_TIMESTAMP=$(date -u +%Y%m%d%H%M%S)
CORPUS_WORK_DIR="${PROJECT_ROOT}/artifacts/deployment-corpus/${DEPLOYMENT_TIMESTAMP}"
CORPUS_NORMALIZED_DIR="${CORPUS_WORK_DIR}/normalized"
CORPUS_INDEX_PATH="${CORPUS_NORMALIZED_DIR}/publications.jsonl"
CORPUS_MANIFEST_PATH="${CORPUS_WORK_DIR}/manifest.json"
CORPUS_MANIFEST_OBJECT=""
PLACEHOLDER_DIGEST=0000000000000000000000000000000000000000000000000000000000000000
PLACEHOLDER_IMAGE="${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT_ID}/${REPOSITORY_NAME}/${IMAGE_NAME}@sha256:${PLACEHOLDER_DIGEST}"

for command_name in gcloud terraform uv; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "missing required command: ${command_name}" >&2
    exit 2
  fi
done

ACTIVE_ACCOUNT=$(gcloud config get-value account 2>/dev/null)
if [[ -z "${ACTIVE_ACCOUNT}" || "${ACTIVE_ACCOUNT}" == "(unset)" ]]; then
  echo "gcloud has no active authenticated account" >&2
  exit 2
fi
if [[ "${ACTIVE_ACCOUNT}" == *".gserviceaccount.com" ]]; then
  API_INVOKER="serviceAccount:${ACTIVE_ACCOUNT}"
else
  API_INVOKER="user:${ACTIVE_ACCOUNT}"
fi

gcloud config set project "${GCP_PROJECT_ID}" >/dev/null

if [[ -d "${PROJECT_ROOT}/data" ]] \
  && find "${PROJECT_ROOT}/data" -type f ! -name ".DS_Store" -print -quit | grep -q .; then
  mkdir -p "${CORPUS_WORK_DIR}"
  SOURCE_HASH_BEFORE=$(
    find "${PROJECT_ROOT}/data" -type f -print0 \
      | sort -z \
      | xargs -0 sha256sum \
      | sha256sum \
      | awk '{print $1}'
  )
  uv run python -m defense_research_agent.cli.ingest \
    --input "${PROJECT_ROOT}/data" \
    --output "${CORPUS_NORMALIZED_DIR}" \
    --report "${CORPUS_WORK_DIR}/ingestion_report.json"
  SOURCE_HASH_AFTER=$(
    find "${PROJECT_ROOT}/data" -type f -print0 \
      | sort -z \
      | xargs -0 sha256sum \
      | sha256sum \
      | awk '{print $1}'
  )
  if [[ "${SOURCE_HASH_BEFORE}" != "${SOURCE_HASH_AFTER}" ]]; then
    echo "source data changed during read-only ingestion; refusing deployment" >&2
    exit 1
  fi

  if [[ "${DEFENSE_RESEARCH_APPROVE_CORPUS:-}" != "1" ]]; then
    echo "Corpus candidate: ${CORPUS_INDEX_PATH}"
    echo "Review the normalized index and ingestion_report.json before approval."
    read -r -p "Approve this public-only corpus for private GCS upload? [y/N] " CORPUS_APPROVAL
    if [[ "${CORPUS_APPROVAL}" != "y" && "${CORPUS_APPROVAL}" != "Y" ]]; then
      echo "corpus approval was not granted; deploying with official web search only"
      CORPUS_INDEX_PATH=""
    fi
  fi

  if [[ -n "${CORPUS_INDEX_PATH}" ]]; then
    CORPUS_REVIEWER=${DEFENSE_RESEARCH_CORPUS_REVIEWER:-"${ACTIVE_ACCOUNT}"}
    CORPUS_MANIFEST_JSON=$(
      uv run python -m defense_research_agent.cli.corpus_index \
        --index "${CORPUS_INDEX_PATH}" \
        --output "${CORPUS_MANIFEST_PATH}" \
        --reviewed-by "${CORPUS_REVIEWER}"
    )
    CORPUS_INDEX_OBJECT=$(
      uv run python -c 'import json, sys; print(json.load(sys.stdin)["index_object"])' \
        <<<"${CORPUS_MANIFEST_JSON}"
    )
    CORPUS_MANIFEST_OBJECT=$(
      uv run python -c 'import json, sys; print(json.load(sys.stdin)["manifest_object"])' \
        <<<"${CORPUS_MANIFEST_JSON}"
    )
  fi
else
  echo "No local data files found; deploying with official web search only."
fi

terraform -chdir="${DEPLOY_DIR}" init
terraform -chdir="${DEPLOY_DIR}" apply -auto-approve \
  -target=google_project_service.required \
  -target=google_artifact_registry_repository.app \
  -target=google_secret_manager_secret.anthropic \
  -target=google_storage_bucket.corpus \
  -var="project_id=${GCP_PROJECT_ID}" \
  -var="region=${GCP_REGION}" \
  -var="app_image=${PLACEHOLDER_IMAGE}" \
  -var="anthropic_secret_version=1"

SECRET_VERSION=$(gcloud secrets versions list "${SECRET_ID}" \
  --project="${GCP_PROJECT_ID}" \
  --filter="state=ENABLED" \
  --sort-by="~name" \
  --limit=1 \
  --format="value(name)")

if [[ -z "${SECRET_VERSION}" ]]; then
  if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
    CLAUDE_KEY=${ANTHROPIC_API_KEY}
  else
    read -r -s -p "Claude API key: " CLAUDE_KEY
    echo
  fi
  if [[ -z "${CLAUDE_KEY}" ]]; then
    echo "Claude API key must not be empty" >&2
    exit 2
  fi
  printf '%s' "${CLAUDE_KEY}" | gcloud secrets versions add "${SECRET_ID}" \
    --project="${GCP_PROJECT_ID}" \
    --data-file=-
  unset CLAUDE_KEY
  SECRET_VERSION=$(gcloud secrets versions list "${SECRET_ID}" \
    --project="${GCP_PROJECT_ID}" \
    --filter="state=ENABLED" \
    --sort-by="~name" \
    --limit=1 \
    --format="value(name)")
fi
SECRET_VERSION=${SECRET_VERSION##*/}

if [[ -n "${CORPUS_MANIFEST_OBJECT}" ]]; then
  CORPUS_INDEX_URI="gs://${CORPUS_BUCKET}/${CORPUS_INDEX_OBJECT}"
  CORPUS_MANIFEST_URI="gs://${CORPUS_BUCKET}/${CORPUS_MANIFEST_OBJECT}"
  if ! gcloud storage objects describe "${CORPUS_INDEX_URI}" \
    --project="${GCP_PROJECT_ID}" >/dev/null 2>&1; then
    gcloud storage cp \
      --if-generation-match=0 \
      "${CORPUS_INDEX_PATH}" \
      "${CORPUS_INDEX_URI}"
  fi
  if ! gcloud storage objects describe "${CORPUS_MANIFEST_URI}" \
    --project="${GCP_PROJECT_ID}" >/dev/null 2>&1; then
    gcloud storage cp \
      --if-generation-match=0 \
      "${CORPUS_MANIFEST_PATH}" \
      "${CORPUS_MANIFEST_URI}"
  fi
fi

BUILD_TAG="p020-${DEPLOYMENT_TIMESTAMP}"
IMAGE_TAG="${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT_ID}/${REPOSITORY_NAME}/${IMAGE_NAME}:${BUILD_TAG}"
gcloud builds submit "${PROJECT_ROOT}" \
  --project="${GCP_PROJECT_ID}" \
  --region="${GCP_REGION}" \
  --tag="${IMAGE_TAG}" \
  --ignore-file="${PROJECT_ROOT}/.gcloudignore"

IMAGE_DIGEST=$(gcloud artifacts docker images describe "${IMAGE_TAG}" \
  --project="${GCP_PROJECT_ID}" \
  --format="value(image_summary.digest)")
if [[ "${IMAGE_DIGEST}" != sha256:* ]]; then
  echo "failed to resolve immutable image digest" >&2
  exit 1
fi
APP_IMAGE="${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT_ID}/${REPOSITORY_NAME}/${IMAGE_NAME}@${IMAGE_DIGEST}"

export TF_VAR_project_id="${GCP_PROJECT_ID}"
export TF_VAR_region="${GCP_REGION}"
export TF_VAR_app_image="${APP_IMAGE}"
export TF_VAR_anthropic_secret_version="${SECRET_VERSION}"
export TF_VAR_api_invoker_members="[\"${API_INVOKER}\"]"
export TF_VAR_firestore_location="${GCP_REGION}"
export TF_VAR_corpus_manifest_object="${CORPUS_MANIFEST_OBJECT}"

if gcloud firestore databases describe \
  --project="${GCP_PROJECT_ID}" \
  --database="(default)" >/dev/null 2>&1; then
  EXISTING_FIRESTORE_LOCATION=$(gcloud firestore databases describe \
    --project="${GCP_PROJECT_ID}" \
    --database="(default)" \
    --format="value(locationId)")
  if [[ -n "${EXISTING_FIRESTORE_LOCATION}" ]]; then
    export TF_VAR_firestore_location="${EXISTING_FIRESTORE_LOCATION}"
  fi
  if ! terraform -chdir="${DEPLOY_DIR}" state show google_firestore_database.app \
    >/dev/null 2>&1; then
    terraform -chdir="${DEPLOY_DIR}" import \
      google_firestore_database.app \
      "projects/${GCP_PROJECT_ID}/databases/(default)"
  fi
fi

terraform -chdir="${DEPLOY_DIR}" apply

API_URL=$(terraform -chdir="${DEPLOY_DIR}" output -raw api_url)
DEPLOYED_CORPUS_MANIFEST=$(terraform -chdir="${DEPLOY_DIR}" output -raw corpus_manifest_object)
echo "Deployment complete."
echo "API URL: ${API_URL}"
if [[ -n "${DEPLOYED_CORPUS_MANIFEST}" ]]; then
  echo "Approved corpus manifest: ${DEPLOYED_CORPUS_MANIFEST}"
else
  echo "Approved corpus manifest: disabled (official web search remains enabled)"
fi
echo "Health:"
echo "curl -H \"Authorization: Bearer \$(gcloud auth print-identity-token)\" \"${API_URL}/healthz\""
