#!/bin/bash

# Exit immediately if a command exits with a non-zero status.
set -e

echo "--- 1. Running data pipeline to generate new cards (idx 87) ---"
python -m pipeline.pipeline mock --idx-min 87 --idx-max 87

echo ""
echo "--- 2. Staging all changes ---"
git add .

echo ""
echo "--- 3. Committing changes ---"
git commit -m "Auto-deploy: UI/UX overhaul, RAG pipeline integration, and new card generation"

echo ""
echo "--- 4. Pushing to main to trigger Render.com deployment ---"
git push origin main

echo ""
echo "--- ✅ Deployment triggered successfully! ---"
