#!/usr/bin/env bash
set -euo pipefail

release_version="$(python scripts/version.py release | sed -n 's/^version=//p')"
release_draft="${INPUT_DRAFT:-false}"
release_prerelease="${INPUT_PRERELEASE:-false}"
target_sha="${RELEASE_TARGET_SHA:?RELEASE_TARGET_SHA is required}"

if [[ "${GITHUB_REF}" != "refs/heads/main" && "${GITHUB_EVENT_NAME}" != "workflow_run" ]]; then
  echo "Releases must be dispatched from main." >&2
  exit 1
fi

should_release="true"

if gh release view "v${release_version}" >/dev/null 2>&1; then
  echo "Version v${release_version} already has a GitHub release; skipping release."
  should_release="false"
elif git rev-parse "v${release_version}" >/dev/null 2>&1; then
  tag_sha="$(git rev-list -n 1 "v${release_version}")"
  if [[ "${tag_sha}" != "${target_sha}" ]]; then
    echo "Tag v${release_version} points to ${tag_sha}, expected ${target_sha}." >&2
    exit 1
  fi

  echo "Version v${release_version} already has a matching tag; creating missing GitHub release."
fi

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  {
    printf 'version=%s\n' "${release_version}"
    printf 'draft=%s\n' "${release_draft}"
    printf 'prerelease=%s\n' "${release_prerelease}"
    printf 'should_release=%s\n' "${should_release}"
  } >> "${GITHUB_OUTPUT}"
else
  printf 'version=%s\n' "${release_version}"
  printf 'draft=%s\n' "${release_draft}"
  printf 'prerelease=%s\n' "${release_prerelease}"
  printf 'should_release=%s\n' "${should_release}"
fi
