#!/bin/bash
# Check if Flask routes have changed since last gateway deploy
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$(dirname "$SCRIPT_DIR")"

cd "$BACKEND_DIR"
python infra/generate_openapi.py > /dev/null 2>&1

if [ -f "infra/openapi.json.prev" ]; then
    if diff -q infra/openapi.json infra/openapi.json.prev > /dev/null 2>&1; then
        echo "No drift. Gateway matches Flask routes."
    else
        echo "DRIFT DETECTED. Routes have changed since last deploy:"
        diff --unified=3 infra/openapi.json.prev infra/openapi.json | head -50
        echo ""
        echo "Run: ./infra/sync_gateway.sh to update"
    fi
else
    echo "No previous deployment found. Run: ./infra/sync_gateway.sh"
fi
