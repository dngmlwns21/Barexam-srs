# Phase 4: Data Execution Script (PowerShell)
Write-Host "Starting Auto-Deployment Sequence..."

# 1. Run Data Pipeline (Small Batch)
Write-Host "Running Data Pipeline (Mock Exam 87)..."
python -m data_pipeline.pipeline mock --idx-min 87 --idx-max 87
if ($LASTEXITCODE -ne 0) { Write-Error "Pipeline failed"; exit 1 }

# 2. Git Operations
Write-Host "Staging files..."
git add .

Write-Host "Committing..."
git commit -m "Auto-deploy: UI/UX overhaul, RAG pipeline integration, and new card generation"

Write-Host "Pushing to origin..."
git push origin main

Write-Host "Deployment Triggered Successfully!"
