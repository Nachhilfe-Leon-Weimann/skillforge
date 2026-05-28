#!/usr/bin/env bash
set -euo pipefail

deploy_webhook_url="${DEPLOY_WEBHOOK_URL:-}"
release_version="${RELEASE_VERSION:?RELEASE_VERSION is required}"
release_target_sha="${RELEASE_TARGET_SHA:?RELEASE_TARGET_SHA is required}"
repository="${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"

if [[ -z "${deploy_webhook_url}" ]]; then
  echo "DEPLOY_WEBHOOK_URL is required to trigger deployment." >&2
  exit 1
fi

image="ghcr.io/$(printf '%s' "${repository}" | tr '[:upper:]' '[:lower:]'):v${release_version}"
payload="$(jq -n \
  --arg repository "${repository}" \
  --arg version "${release_version}" \
  --arg image "${image}" \
  --arg sha "${release_target_sha}" \
  '{repository: $repository, version: $version, image: $image, sha: $sha}')"

curl_args=(
  --fail
  --show-error
  --silent
  --request POST
  --header "Content-Type: application/json"
  --data "${payload}"
)

curl "${curl_args[@]}" "${deploy_webhook_url}"
