#!/usr/bin/env bash
set -euo pipefail

release_version="${RELEASE_VERSION:?RELEASE_VERSION is required}"
release_draft="${RELEASE_DRAFT:-false}"
release_prerelease="${RELEASE_PRERELEASE:-false}"
release_target_sha="${RELEASE_TARGET_SHA:?RELEASE_TARGET_SHA is required}"
repository="${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"

image="ghcr.io/$(printf '%s' "${repository}" | tr '[:upper:]' '[:lower:]'):v${release_version}"
notes=$'Container image:\n\n'"${image}"

args=(
  "v${release_version}"
  --target "${release_target_sha}"
  --title "v${release_version}"
  --generate-notes
  --notes "${notes}"
)

if [[ "${release_draft}" == "true" ]]; then
  args+=(--draft)
fi

if [[ "${release_prerelease}" == "true" ]]; then
  args+=(--prerelease)
fi

gh release create "${args[@]}"
