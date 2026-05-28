#!/usr/bin/env bash
set -euo pipefail

target_sha="${WORKFLOW_RUN_HEAD_SHA:-${GITHUB_SHA}}"

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  printf 'sha=%s\n' "${target_sha}" >> "${GITHUB_OUTPUT}"
else
  printf '%s\n' "${target_sha}"
fi
