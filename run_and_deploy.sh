#!/bin/bash
# Phase 4: Data Execution Script (Bash)
echo "Starting Auto-Deployment Sequence..."

# 1. Run Data Pipeline
echo "Running Data Pipeline (Mock Exam 87)..."
python -m data_pipeline.pipeline mock --idx-min 87 --idx-max 87 --wipe
if [ $? -ne 0 ]; then
    echo "Pipeline failed"
    exit 1
fi

# 2. Git Operations
echo "Staging files..."
git add .

echo "Committing..."
git commit -m "Auto-deploy: UI/UX overhaul, RAG pipeline integration, and new card generation"

echo "Pushing to origin..."
git push origin main

echo "Deployment Triggered Successfully!"
