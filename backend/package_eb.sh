#!/usr/bin/env bash
#
# package_eb.sh — Build an Elastic Beanstalk deploy zip from backend/
#
# Output: backend-<tag>-<eb-env>.zip at the repo root, mirroring the
# existing convention (e.g. backend-staging-asv-dev.zip).
#
# Usage:
#   ./package_eb.sh                    # tag=staging, env from .elasticbeanstalk/config.yml
#   ./package_eb.sh prod               # tag=prod,    env from .elasticbeanstalk/config.yml
#   ./package_eb.sh staging asv-prod   # tag=staging, env=asv-prod (override)
#
# The zip flattens backend/ to the archive root (so app.py is at /app.py
# inside the zip, not /backend/app.py) which is what EB expects.

set -euo pipefail

# --- Resolve paths -----------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$SCRIPT_DIR"

# --- Args & EB env detection -------------------------------------------------
TAG="${1:-staging}"

if [[ -n "${2:-}" ]]; then
  EB_ENV="$2"
else
  EB_CONFIG="$SCRIPT_DIR/.elasticbeanstalk/config.yml"
  if [[ ! -f "$EB_CONFIG" ]]; then
    echo "ERROR: $EB_CONFIG not found — pass the EB env name as the 2nd arg." >&2
    exit 1
  fi
  EB_ENV="$(awk '/^[[:space:]]*default:/{f=1; next} f && /environment:/{print $2; exit}' "$EB_CONFIG")"
  if [[ -z "$EB_ENV" ]]; then
    echo "ERROR: could not parse default environment from $EB_CONFIG — pass it explicitly." >&2
    exit 1
  fi
fi

OUT_ZIP="$REPO_ROOT/backend-${TAG}-${EB_ENV}.zip"

# --- Pre-flight checks -------------------------------------------------------
command -v zip >/dev/null 2>&1 || { echo "ERROR: 'zip' not installed." >&2; exit 1; }

[[ -f "$SCRIPT_DIR/app.py" ]] || { echo "ERROR: app.py not found in $SCRIPT_DIR" >&2; exit 1; }
[[ -f "$SCRIPT_DIR/requirements.txt" ]] || { echo "ERROR: requirements.txt missing." >&2; exit 1; }
[[ -f "$SCRIPT_DIR/infra/openapi.json" ]] || { echo "ERROR: infra/openapi.json missing — run 'python infra/generate_openapi.py' first." >&2; exit 1; }

# Soft-warn if openapi.json hasn't been regenerated since app.py changed.
if [[ "$SCRIPT_DIR/app.py" -nt "$SCRIPT_DIR/infra/openapi.json" ]]; then
  echo "WARNING: app.py is newer than infra/openapi.json."
  echo "         If you added/changed Flask routes, run:"
  echo "           python infra/generate_openapi.py"
  echo "         and then re-run this script. Continuing in 3s..."
  sleep 3
fi

echo "==> Packaging backend for EB"
echo "    repo root : $REPO_ROOT"
echo "    backend   : $SCRIPT_DIR"
echo "    tag       : $TAG"
echo "    eb env    : $EB_ENV"
echo "    output    : $OUT_ZIP"
echo

# --- Clean stale bytecode so it doesn't bloat the zip ------------------------
find "$SCRIPT_DIR" -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true
find "$SCRIPT_DIR" -type f -name "*.pyc" -delete 2>/dev/null || true

# --- Build the zip -----------------------------------------------------------
# Exclusions mirror .ebignore plus a few extras (logs, venv, app_versions, etc).
rm -f "$OUT_ZIP"

# Run zip from inside backend/ so paths in the archive are relative to backend/.
( cd "$SCRIPT_DIR" && zip -r "$OUT_ZIP" . \
    -x ".git/*" \
    -x ".gitignore" \
    -x ".ebignore" \
    -x ".elasticbeanstalk/app_versions/*" \
    -x ".elasticbeanstalk/logs/*" \
    -x ".platform/nginx/conf.d/*.backup" \
    -x "venv/*" "*/venv/*" \
    -x ".venv/*" "*/.venv/*" \
    -x "__pycache__/*" "*/__pycache__/*" \
    -x "*.pyc" "*.pyo" "*.pyd" \
    -x ".pytest_cache/*" \
    -x ".vscode/*" ".idea/*" \
    -x ".DS_Store" "*/.DS_Store" \
    -x "*.log" "*/*.log" \
    -x ".env" ".env.*" \
    -x "logs/*" \
    -x "openai.ini" \
    -x "infra/cdk/cdk.out/*" \
    -x "infra/openapi.json.prev" \
    -x "*.zip" \
    > /dev/null )

# --- Quick sanity checks on the produced zip ---------------------------------
if [[ ! -s "$OUT_ZIP" ]]; then
  echo "ERROR: zip is missing or empty." >&2
  exit 1
fi

# NB: matched with awk, not grep -E "\s". This check reported ERROR on two
# perfectly valid bundles because \s is not portable across the greps found on
# macOS — and an ERROR that is routinely wrong is worse than no check, because
# it trains you to deploy through it.
unzip -l "$OUT_ZIP" | awk '$NF == "app.py" { found=1 } END { exit !found }' || {
  echo "ERROR: zip does not contain app.py at the archive root." >&2
  exit 1
}

unzip -l "$OUT_ZIP" | grep -q "^\s*[0-9].*\s\+infra/openapi\.json$" || {
  echo "WARNING: zip does not contain infra/openapi.json — this is fine for EB,"
  echo "         but CDK won't see your latest route changes from here."
}

FILE_COUNT="$(unzip -l "$OUT_ZIP" | tail -1 | awk '{print $2}')"
SIZE_BYTES="$(wc -c < "$OUT_ZIP" | tr -d ' ')"
SIZE_MB="$(awk -v b="$SIZE_BYTES" 'BEGIN{printf "%.2f", b/1024/1024}')"

echo "==> Done."
echo "    files : $FILE_COUNT"
echo "    size  : ${SIZE_MB} MB"
echo "    path  : $OUT_ZIP"
echo
echo "Next: deploy via EB CLI"
echo "    eb deploy $EB_ENV --staged   # if you stage the zip"
echo "  or upload the zip in the EB console under the '$EB_ENV' environment."
