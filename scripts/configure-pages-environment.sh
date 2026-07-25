#!/usr/bin/env bash
#
# configure-pages-environment.sh
# -----------------------------------------------------------------------------
# One-time (idempotent, safe to re-run) configuration of GitHub Pages + Actions
# for the Betbot repository, so the `deploy-pages.yml` workflow can publish the
# site automatically with zero manual clicking in the GitHub UI.
#
# What it does:
#   1. Enables GitHub Pages with the "GitHub Actions" build type (build_type=workflow).
#   2. Ensures the branch-based publishing fallback reference is `main`.
#   3. Grants the repo's default GITHUB_TOKEN read/write workflow permissions.
#   4. Ensures Actions are enabled for the repo (all actions allowed).
#   5. Prints the live Pages URL and saves it to PAGES_URL.txt in the repo root.
#
# Requirements:
#   - `gh` (GitHub CLI) authenticated with a token that has admin rights on the repo.
#   - `jq` is NOT required (we use gh's --jq).
#
# Usage:
#   ./scripts/configure-pages-environment.sh [owner] [repo]
#   OWNER=myorg REPO=myrepo ./scripts/configure-pages-environment.sh
#
# Defaults target this repository.
# -----------------------------------------------------------------------------
set -euo pipefail

# --- Resolve owner/repo from args, then env vars, then sensible defaults. ------
OWNER="${1:-${OWNER:-lherrera31820-hub}}"
REPO="${2:-${REPO:-Betbot-}}"
BRANCH="${BRANCH:-main}"
SLUG="${OWNER}/${REPO}"

echo ">> Configuring GitHub Pages + Actions for: ${SLUG} (branch: ${BRANCH})"

# Helper: run a gh api call but don't abort the whole script on a single
# expected failure (e.g. POST when the resource already exists). We inspect
# and fall back where needed.
gh_api() { gh api "$@"; }

# --- 1. Ensure Actions are enabled for the repo. ------------------------------
echo ">> [1/4] Ensuring GitHub Actions are enabled (all actions allowed)..."
gh_api -X PUT "repos/${SLUG}/actions/permissions" \
  -f enabled=true \
  -f allowed_actions=all \
  && echo "   Actions enabled." \
  || echo "   WARN: could not update Actions permissions (may lack admin scope)."

# --- 2. Grant read/write default workflow permissions. ------------------------
echo ">> [2/4] Setting default workflow permissions to read/write..."
gh_api -X PUT "repos/${SLUG}/actions/permissions/workflow" \
  -f default_workflow_permissions=write \
  -F can_approve_pull_request_reviews=false \
  && echo "   Default workflow permissions = write." \
  || echo "   WARN: could not update workflow permissions."

# --- 3. Enable GitHub Pages with the Actions (workflow) build type. -----------
# POST creates the Pages site; if it already exists GitHub returns 409, so we
# fall back to PATCH to (re)assert the desired configuration.
echo ">> [3/4] Enabling GitHub Pages with build_type=workflow..."
if gh_api -X POST "repos/${SLUG}/pages" -f build_type=workflow >/dev/null 2>&1; then
  echo "   Pages created with build_type=workflow."
else
  echo "   Pages already exists (or POST rejected); asserting config via PATCH..."
  # Assert the Actions build type.
  gh_api -X PUT "repos/${SLUG}/pages" -f build_type=workflow >/dev/null 2>&1 \
    || gh_api -X PATCH "repos/${SLUG}/pages" -f build_type=workflow >/dev/null 2>&1 \
    || echo "   WARN: could not update Pages build_type."
  # Ensure any branch-based fallback source points at ${BRANCH}/root.
  # (Harmless when build_type=workflow; keeps branch fallback correct.)
  gh_api -X PUT "repos/${SLUG}/pages" \
    -f "source[branch]=${BRANCH}" \
    -f "source[path]=/" >/dev/null 2>&1 \
    || gh_api -X PATCH "repos/${SLUG}/pages" \
        -f "source[branch]=${BRANCH}" \
        -f "source[path]=/" >/dev/null 2>&1 \
    || true
  echo "   Pages configuration asserted (build_type=workflow, fallback branch=${BRANCH})."
fi

# --- 4. Fetch and report the live Pages URL. ---------------------------------
echo ">> [4/4] Fetching live Pages URL..."
PAGES_URL="$(gh_api "repos/${SLUG}/pages" --jq .html_url 2>/dev/null || true)"

if [ -n "${PAGES_URL}" ] && [ "${PAGES_URL}" != "null" ]; then
  # Save to repo root regardless of where the script is invoked from.
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
  printf '%s\n' "${PAGES_URL}" > "${REPO_ROOT}/PAGES_URL.txt"
  echo ""
  echo "============================================================"
  echo "  GitHub Pages is live at:"
  echo "    ${PAGES_URL}"
  echo "  Monitoring dashboard:"
  echo "    ${PAGES_URL%/}/dashboard/"
  echo "  (saved to PAGES_URL.txt)"
  echo "============================================================"
else
  echo "   NOTE: Pages URL not available yet. It appears after the first"
  echo "         successful deploy run. Re-run this script later to capture it."
fi

echo ">> Done."
