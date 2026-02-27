#!/bin/bash
# run_and_deploy.sh — Auto-deploy: RAG pipeline + git push to Render.com
# Usage: bash run_and_deploy.sh
set -euo pipefail

echo "=== [1/4] Running data pipeline (mock idx 87) ==="
python -m data_pipeline.pipeline mock --idx-min 87 --idx-max 87

echo "=== [2/4] Staging all changes ==="
git add .

echo "=== [3/4] Committing ==="
git commit -m "Auto-deploy: UI/UX overhaul, RAG pipeline integration, and new card generation"

echo "=== [4/4] Pushing to origin main ==="
git push https://${GITHUB_TOKEN}@github.com/dngmlwns21/Barexam-srs.git main

echo "=== Deploy complete! Render.com will auto-deploy from main branch ==="
