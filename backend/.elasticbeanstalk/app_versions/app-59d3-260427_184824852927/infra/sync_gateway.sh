#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== Step 1: Generate OpenAPI spec from Flask routes ==="
cd "$BACKEND_DIR"
python infra/generate_openapi.py

echo ""
echo "=== Step 2: Diff current vs previous spec ==="
if [ -f "infra/openapi.json.prev" ]; then
    if diff -q infra/openapi.json infra/openapi.json.prev > /dev/null 2>&1; then
        echo "No route changes detected. Gateway is up to date."
        read -p "Force deploy anyway? (y/N): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            echo "Skipping deploy."
            exit 0
        fi
    else
        echo "Route changes detected:"
        diff infra/openapi.json.prev infra/openapi.json || true
    fi
else
    echo "No previous spec found. First deployment."
fi

echo ""
echo "=== Step 3: CDK diff (preview changes) ==="
cd "$SCRIPT_DIR/cdk"
pip install -r requirements.txt -q
npx --yes aws-cdk diff 2>&1 || true

echo ""
echo "=== Step 4: CDK deploy ==="
npx --yes aws-cdk deploy --require-approval never

echo ""
echo "=== Step 5: Save current spec as previous ==="
cp "$BACKEND_DIR/infra/openapi.json" "$BACKEND_DIR/infra/openapi.json.prev"

echo ""
echo "Gateway updated successfully."
