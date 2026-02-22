#!/bin/bash
# Phase 4: Data Execution Script (Bash)
echo "Starting Auto-Deployment Sequence..."

# 1. Run Data Pipeline
echo "Running Data Pipeline (Full Mock Exam)..."
python -m data_pipeline.pipeline mock
if [ $? -ne 0 ]; then
    echo "Pipeline failed"
    exit 1
fi

# 2. Git Operations
echo "Staging files..."
git add .

echo "Committing..."
git commit -m "chore: Process all mock data and deploy"

echo "Pushing to origin..."
git push origin main

echo "Deployment Triggered Successfully!"
