#!/usr/bin/env bash
#
# Weekly NYC/NYS fiscal policy digest — local host cron entrypoint.
#
# Refreshes main, builds the Docker image, runs the pipeline exactly as the
# old GitHub Action did, then (on changes) opens a PR to the branch-protected
# main via the GitHub REST API. Any hard failure or PR-creation failure is
# announced in the Discord channel so a broken cron never fails silently.
#
# Env comes from a gitignored .env at the repo root. Install as e.g.:
#   7 11 * * 1  /path/to/repo/scripts/run_weekly.sh >> /var/log/digest.log 2>&1
set -euo pipefail

# --- locate repo root (script may be invoked from anywhere) -----------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

DATE="$(date -u +%F)"
BRANCH="digest/${DATE}"

# --- tooling preflight ------------------------------------------------------
for cmd in docker git curl jq; do
  command -v "${cmd}" >/dev/null 2>&1 || { echo "ERROR: '${cmd}' is required but not installed" >&2; exit 1; }
done

# --- load .env and validate required secrets --------------------------------
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

missing=()
for var in ANTHROPIC_API_KEY DISCORD_WEBHOOK_URL NYSENATE_API_KEY GITHUB_TOKEN; do
  [[ -n "${!var:-}" ]] || missing+=("${var}")
done
if (( ${#missing[@]} > 0 )); then
  echo "ERROR: missing required env var(s): ${missing[*]} (set them in ${REPO_ROOT}/.env)" >&2
  exit 1
fi

# Post a short plain-content failure notice (no @everyone) to Discord. Never
# echoes the webhook URL; tolerates its own failure so it can't mask the
# original error.
notify_failure() {
  local message="$1"
  local payload
  payload="$(jq -n --arg c "${message}" '{content: $c, allowed_mentions: {parse: []}}')"
  if ! curl -fsS -X POST -H 'Content-Type: application/json' \
        --data "${payload}" "${DISCORD_WEBHOOK_URL}?wait=true" >/dev/null 2>&1; then
    echo "WARN: failed to deliver failure notice to Discord" >&2
  fi
}

# --- refresh main -----------------------------------------------------------
git checkout main
if ! git pull --ff-only origin main; then
  echo "ERROR: 'git pull' failed — resolve the working tree and re-run" >&2
  exit 1
fi

# --- idempotency: one PR per day --------------------------------------------
if git ls-remote --exit-code --heads origin "${BRANCH}" >/dev/null 2>&1; then
  echo "Branch ${BRANCH} already exists on origin — nothing to do."
  exit 0
fi

# --- build image and run the pipeline ---------------------------------------
docker build -t digest:latest .

set +e
docker run --rm -v "${PWD}:/app" \
  -e ANTHROPIC_API_KEY -e DISCORD_WEBHOOK_URL -e NYSENATE_API_KEY \
  digest:latest python -m pipeline.main
run_rc=$?
set -e

if (( run_rc != 0 )); then
  err="see host log"
  [[ -f run_summary.json ]] && err="$(jq -r '.error // "see host log"' run_summary.json)"
  notify_failure "⚠️ Weekly digest run failed on ${DATE}: ${err}"
  echo "ERROR: pipeline exited ${run_rc}" >&2
  exit 1
fi

# --- nothing to commit? quiet week, no PR needed ----------------------------
if [[ -z "$(git status --porcelain -- state/seen.json digests/)" ]]; then
  echo "No changes to state/seen.json or digests/ — quiet week, no PR."
  exit 0
fi

# --- collect soft failures for the PR body ----------------------------------
soft_failures=""
if [[ -f run_summary.json ]]; then
  soft_failures="$(jq -r '
    ((.source_failures // [])[]   | "- Source `\(.source_id // .source)`: \(.error)"),
    ((.delivery_failures // [])[] | "- Delivery (\(.name)): \(.error)")
  ' run_summary.json)"
fi

# --- branch, commit, push ---------------------------------------------------
git checkout -b "${BRANCH}"
git add state/seen.json digests/
git commit -m "digest: ${DATE}"
git push -u origin "${BRANCH}"

# --- build the PR body (mirrors PULL_REQUEST_TEMPLATE.md when present) -------
body_file="$(mktemp)"
trap 'rm -f "${body_file}"' EXIT

if [[ -f .github/PULL_REQUEST_TEMPLATE.md ]]; then
  cat > "${body_file}" <<EOF
## Summary

Automated weekly NYC/NYS fiscal policy digest for ${DATE}.

## Why

Scheduled weekly run via the local host cron (\`scripts/run_weekly.sh\`).

## Changes

- Added/updated this week's digest under \`digests/\`.
- Updated \`state/seen.json\` dedup memory.

## Verification

Pipeline ran in send mode (\`python -m pipeline.main\`) inside \`digest:latest\`;
this PR is the data commit for that run. The Discord post is delivered by the
run itself.
EOF
  if [[ -n "${soft_failures}" ]]; then
    printf '\n## Run notes (soft failures)\n\nThe run succeeded but these components failed:\n\n%s\n' "${soft_failures}" >> "${body_file}"
  fi
  cat >> "${body_file}" <<'EOF'

## Checklist

- [x] **CLAUDE.md** — no behavior change (automated data commit)
- [x] `make digest-dry` not applicable (automated send run)
- [x] No secrets, phone numbers, or other PII committed
- [x] Editorial neutrality guardrails untouched
EOF
else
  printf 'Automated weekly NYC/NYS fiscal policy digest for %s.\n' "${DATE}" > "${body_file}"
  [[ -n "${soft_failures}" ]] && printf '\nSoft failures this run:\n\n%s\n' "${soft_failures}" >> "${body_file}"
fi

# --- open the PR via the GitHub REST API ------------------------------------
slug="$(git config --get remote.origin.url | sed -E 's#^(git@github.com:|https://github.com/)##; s#\.git$##')"
pr_payload="$(jq -n --arg t "digest: ${DATE}" --arg h "${BRANCH}" \
  --rawfile b "${body_file}" '{title: $t, head: $h, base: "main", body: $b}')"

resp_file="$(mktemp)"
trap 'rm -f "${body_file}" "${resp_file}"' EXIT
http_code="$(curl -sS -o "${resp_file}" -w '%{http_code}' -X POST \
  -H "Authorization: Bearer ${GITHUB_TOKEN}" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "https://api.github.com/repos/${slug}/pulls" \
  --data "${pr_payload}")" || http_code="000"

if [[ "${http_code}" != 2* ]]; then
  api_err="$(jq -r '.message // "unknown error"' "${resp_file}" 2>/dev/null || echo "unknown error")"
  notify_failure "⚠️ Weekly digest ${DATE}: pushed ${BRANCH} but PR creation failed (HTTP ${http_code}: ${api_err})."
  echo "ERROR: PR creation failed (HTTP ${http_code}): ${api_err}" >&2
  exit 1
fi

pr_url="$(jq -r '.html_url // empty' "${resp_file}")"
echo "Opened PR for ${DATE}: ${pr_url}"
