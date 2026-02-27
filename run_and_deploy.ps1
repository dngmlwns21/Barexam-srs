# run_and_deploy.ps1 — Auto-deploy: RAG pipeline + git push to Render.com
# Usage: .\run_and_deploy.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Write-Host "=== [1/4] Running data pipeline (mock idx 87) ===" -ForegroundColor Cyan
python -m data_pipeline.pipeline mock --idx-min 87 --idx-max 87

Write-Host "=== [2/4] Staging all changes ===" -ForegroundColor Cyan
git add .

Write-Host "=== [3/4] Committing ===" -ForegroundColor Cyan
git commit -m "Auto-deploy: UI/UX overhaul, RAG pipeline integration, and new card generation"

Write-Host "=== [4/4] Pushing to origin main ===" -ForegroundColor Cyan
git push https://${env:GITHUB_TOKEN}@github.com/dngmlwns21/Barexam-srs.git main

Write-Host "=== Deploy complete! Render.com will auto-deploy from main branch ===" -ForegroundColor Green
