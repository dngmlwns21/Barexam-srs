# Stop script on any error
$ErrorActionPreference = "Stop"

Write-Host "--- 1. Running data pipeline to generate new cards (idx 87) ---"
python -m pipeline.pipeline mock --idx-min 87 --idx-max 87

Write-Host ""
Write-Host "--- 2. Staging all changes ---"
git add .

Write-Host ""
Write-Host "--- 3. Committing changes ---"
git commit -m "Auto-deploy: UI/UX overhaul, RAG pipeline integration, and new card generation"

Write-Host ""
Write-Host "--- 4. Pushing to main to trigger Render.com deployment ---"
git push origin main

Write-Host ""
Write-Host "--- ✅ Deployment triggered successfully! ---"
