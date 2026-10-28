#!/usr/bin/env bash
# Deploy the AutoTabber Modal app (GPU separation + CPU transcription).
# Requires MODAL_TOKEN_ID / MODAL_TOKEN_SECRET in the environment,
# or a local Modal profile (~/.modal.toml).
set -euo pipefail
cd "$(dirname "$0")/.."
python -m modal deploy modal_app.py
