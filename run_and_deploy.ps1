# Phase 4: Data Execution Script (PowerShell)
Write-Host "Starting Auto-Deployment Sequence..."

# 1. Run Data Pipeline (Small Batch)
Write-Host "Running Data Pipeline (Full Mock Exam)..."
python -m data_pipeline.pipeline mock
if ($LASTEXITCODE -ne 0) { Write-Error "Pipeline failed"; exit 1 }

# 2. Git Operations
Write-Host "Staging files..."
git add .

Write-Host "Committing..."
git commit -m "chore: Process all mock data and deploy"

Write-Host "Pushing to origin..."
git push origin main

Write-Host "Deployment Triggered Successfully!"
