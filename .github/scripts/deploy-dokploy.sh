#!/usr/bin/env bash
# Deploys a Dokploy compose service through the Dokploy API and verifies the result:
# trigger compose.deploy, wait until the new deployment is done, then expect HEALTH_URL to answer
# with status "ok" and the released version. No rollback by design (docs/specs/release-flow.md).
set -euo pipefail

die()
{
	echo "error: $*" >&2
	exit 1
}

for tool in curl jq; do
	command -v "$tool" > /dev/null || die "$tool is required"
done
for name in DOKPLOY_BASE_URL DOKPLOY_API_KEY DOKPLOY_COMPOSE_ID RELEASE_VERSION; do
	[ -n "${!name:-}" ] || die "$name is required"
done

DOKPLOY_BASE_URL=${DOKPLOY_BASE_URL%/}
HEALTH_URL=${HEALTH_URL:-}
DRY_RUN=${DRY_RUN:-false}
DEPLOY_TIMEOUT=${DOKPLOY_DEPLOY_TIMEOUT_SECONDS:-900}
HEALTH_TIMEOUT=${DOKPLOY_HEALTH_TIMEOUT_SECONDS:-180}
POLL_INTERVAL=${DOKPLOY_POLL_INTERVAL_SECONDS:-5}

# Plain HTTP is only accepted for a local fake (tests).
for url_name in DOKPLOY_BASE_URL HEALTH_URL; do
	url=${!url_name}
	[ -n "$url" ] || continue
	[[ "$url" == https://* || "$url" =~ ^http://(127\.0\.0\.1|localhost)(:[0-9]+)?(/|$) ]] \
		|| die "$url_name must use HTTPS"
	[[ "$url" != *[$'\r\n\t ']* ]] || die "$url_name must not contain whitespace"
done
[[ "$DOKPLOY_API_KEY" =~ ^[A-Za-z0-9._~+/=-]+$ ]] || die "DOKPLOY_API_KEY has an invalid format"
[[ "$DOKPLOY_COMPOSE_ID" =~ ^[A-Za-z0-9_-]+$ ]] || die "DOKPLOY_COMPOSE_ID contains unsupported characters"
[[ "$RELEASE_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([+-][0-9A-Za-z.-]+)?$ ]] \
	|| die "RELEASE_VERSION must be a semantic version without a v prefix"
[[ "$DEPLOY_TIMEOUT" =~ ^[1-9][0-9]*$ && "$HEALTH_TIMEOUT" =~ ^[1-9][0-9]*$ ]] \
	|| die "timeouts must be positive integers"
[[ "$POLL_INTERVAL" =~ ^[0-9]+(\.[0-9]+)?$ ]] || die "DOKPLOY_POLL_INTERVAL_SECONDS must be a number"
[ "$DRY_RUN" = true ] || [ "$DRY_RUN" = false ] || die "DRY_RUN must be true or false"

WORK_DIR=$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/dokploy-deploy.XXXXXX")
trap 'rm -rf -- "$WORK_DIR"' EXIT

# The key goes through a config file so it never shows up in the process list or in logs.
CURL_CONFIG=$WORK_DIR/curl.conf
(
	umask 077
	printf 'header = "Accept: application/json"\nheader = "x-api-key: %s"\n' "$DOKPLOY_API_KEY" > "$CURL_CONFIG"
)

# api METHOD ENDPOINT OUTPUT_FILE [PAYLOAD_FILE] - dies on transport errors and non-2xx answers.
api()
{
	local method=$1 endpoint=$2 output=$3 payload=${4:-} code
	local -a args=(
		--silent --show-error --connect-timeout 10 --max-time 60
		--config "$CURL_CONFIG" --output "$output" --write-out '%{http_code}'
	)
	if [ "$method" = POST ]; then
		args+=(--request POST --header 'Content-Type: application/json' --data-binary "@$payload")
	fi
	code=$(curl "${args[@]}" "$DOKPLOY_BASE_URL/api/$endpoint") \
		|| die "Dokploy API request failed: $method /api/$endpoint"
	[[ "$code" == 2?? ]] \
		|| die "Dokploy returned HTTP $code for $method /api/$endpoint: $(jq -r '.message // "no message"' "$output" 2> /dev/null || echo "unreadable body")"
}

list_deployments()
{
	api GET "deployment.allByCompose?composeId=$DOKPLOY_COMPOSE_ID" "$1"
	jq -e 'type == "array"' "$1" > /dev/null || die "Dokploy returned an invalid deployment list"
}

BEFORE=$WORK_DIR/before.json
list_deployments "$BEFORE"
[ "$(jq -r '.[0].status // empty' "$BEFORE")" != running ] || die "another Dokploy deployment is already running"

if [ "$DRY_RUN" = true ]; then
	echo "dry run: Dokploy API access and the compose service's deployment list verified; nothing deployed"
	exit 0
fi

PAYLOAD=$WORK_DIR/deploy.json
jq -n \
	--arg composeId "$DOKPLOY_COMPOSE_ID" \
	--arg title "Release v$RELEASE_VERSION from GitHub Actions" \
	--arg description "run=${GITHUB_RUN_ID:-local} attempt=${GITHUB_RUN_ATTEMPT:-1} sha=${RELEASE_SHA:-unknown}" \
	'{composeId: $composeId, title: $title, description: $description}' > "$PAYLOAD"
api POST compose.deploy "$WORK_DIR/deploy-response.json" "$PAYLOAD"
echo "Dokploy accepted the deployment request for v$RELEASE_VERSION"

# The new deployment is the newest entry that did not exist before the request - this does not
# depend on Dokploy echoing the title back.
deployment_id=
deadline=$((SECONDS + DEPLOY_TIMEOUT))
while :; do
	((SECONDS < deadline)) || die "Dokploy deployment did not finish within $DEPLOY_TIMEOUT seconds"
	list_deployments "$WORK_DIR/now.json"
	deployment=$(jq -c --slurpfile before "$BEFORE" \
		'[$before[0][].deploymentId] as $known | [.[] | select(.deploymentId | IN($known[]) | not)] | first // empty' \
		"$WORK_DIR/now.json")
	if [ -n "$deployment" ]; then
		deployment_id=$(jq -r '.deploymentId // empty' <<< "$deployment")
		status=$(jq -r '.status // empty' <<< "$deployment")
		echo "Dokploy deployment ${deployment_id:-unknown}: ${status:-unknown}"
		case "$status" in
			done) break ;;
			error | cancelled)
				die "Dokploy deployment $status: $(jq -r '.errorMessage // "no error message returned"' <<< "$deployment")"
				;;
		esac
	fi
	sleep "$POLL_INTERVAL"
done

if [ -n "$HEALTH_URL" ]; then
	deadline=$((SECONDS + HEALTH_TIMEOUT))
	until curl --fail --silent --connect-timeout 5 --max-time 10 --output "$WORK_DIR/health.json" "$HEALTH_URL" \
		&& jq -e --arg version "$RELEASE_VERSION" '.status == "ok" and .version == $version' \
			"$WORK_DIR/health.json" > /dev/null; do
		((SECONDS < deadline)) \
			|| die "$HEALTH_URL did not report status ok with version $RELEASE_VERSION within $HEALTH_TIMEOUT seconds (last answer: $(head -c 300 "$WORK_DIR/health.json" 2> /dev/null || echo none))"
		sleep "$POLL_INTERVAL"
	done
	echo "$HEALTH_URL reports status ok with version $RELEASE_VERSION"
fi

[[ "$deployment_id" =~ ^[A-Za-z0-9_-]+$ ]] || deployment_id=unknown
if [ -n "${GITHUB_OUTPUT:-}" ]; then
	echo "deployment_id=$deployment_id" >> "$GITHUB_OUTPUT"
fi
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
	{
		echo "## Deploy"
		echo
		echo "| Field | Value |"
		echo "| --- | --- |"
		echo "| Version | v$RELEASE_VERSION |"
		echo "| Dokploy deployment | $deployment_id |"
		echo "| Health | ${HEALTH_URL:-not checked (no HTTP endpoint)} |"
	} >> "$GITHUB_STEP_SUMMARY"
fi
echo "v$RELEASE_VERSION deployed and verified"
