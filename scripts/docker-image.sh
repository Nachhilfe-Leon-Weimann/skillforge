#!/usr/bin/env bash
set -euo pipefail

mode="${1:-build}"

repository="${GITHUB_REPOSITORY:-}"
if [[ -z "${repository}" ]]; then
  repository="$(basename "$(git rev-parse --show-toplevel)")"
fi

image="ghcr.io/$(printf '%s' "${repository}" | tr '[:upper:]' '[:lower:]')"
source_sha="$(git rev-parse HEAD)"
short_sha="${source_sha:0:12}"
version="${IMAGE_VERSION:-}"
tag_latest="${IMAGE_TAG_LATEST:-false}"
push_image="${IMAGE_PUSH:-false}"

tags=("${image}:sha-${short_sha}")

if [[ -n "${version}" ]]; then
  tags+=("${image}:v${version}")
fi

if [[ "${tag_latest}" == "true" ]]; then
  tags+=("${image}:latest")
fi

write_outputs() {
  if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    {
      printf 'image=%s\n' "${image}"
      printf 'source_sha=%s\n' "${source_sha}"
      printf 'primary_tag=%s\n' "${tags[0]}"
      if [[ -n "${version}" ]]; then
        printf 'version_tag=%s\n' "${image}:v${version}"
      fi
    } >> "${GITHUB_OUTPUT}"
  fi
}

require_private_repo_token() {
  if [[ -z "${SKILLPLATFORM_READ_TOKEN:-}" ]]; then
    echo "SKILLPLATFORM_READ_TOKEN is required to build the image." >&2
    exit 1
  fi
}

login_to_ghcr() {
  if [[ "${push_image}" != "true" ]]; then
    return
  fi

  if [[ -z "${GHCR_TOKEN:-}" || -z "${GITHUB_ACTOR:-}" ]]; then
    echo "GHCR_TOKEN and GITHUB_ACTOR are required when IMAGE_PUSH=true." >&2
    exit 1
  fi

  echo "${GHCR_TOKEN}" | docker login ghcr.io -u "${GITHUB_ACTOR}" --password-stdin
}

build_image() {
  local docker_args=()

  for tag in "${tags[@]}"; do
    docker_args+=("-t" "${tag}")
  done

  docker build \
    --secret id=github_token,env=SKILLPLATFORM_READ_TOKEN \
    -f dockerfile \
    "${docker_args[@]}" \
    .
}

push_tags() {
  if [[ "${push_image}" != "true" ]]; then
    return
  fi

  for tag in "${tags[@]}"; do
    docker push "${tag}"
  done
}

case "${mode}" in
  --metadata | metadata)
    write_outputs
    ;;
  build)
    require_private_repo_token
    login_to_ghcr
    build_image
    push_tags
    write_outputs
    ;;
  *)
    echo "Usage: $0 [--metadata|build]" >&2
    exit 2
    ;;
esac
