#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."

./scripts/start_backend.sh

if command -v npx.cmd >/dev/null 2>&1; then
    npx.cmd tauri dev
else
    npx tauri dev
fi
