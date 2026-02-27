-- Migration 003: Add OX card columns
-- Safe to re-run (uses IF NOT EXISTS / ALTER ... IF NOT EXISTS pattern via DO blocks)

-- questions: add overall_explanation
DO $$ BEGIN
    ALTER TABLE questions ADD COLUMN overall_explanation TEXT;
EXCEPTION WHEN duplicate_column THEN NULL; END $$;

-- choices: add extended OX fields
DO $$ BEGIN
    ALTER TABLE choices ADD COLUMN legal_basis TEXT;
EXCEPTION WHEN duplicate_column THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE choices ADD COLUMN case_citation TEXT;
EXCEPTION WHEN duplicate_column THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE choices ADD COLUMN explanation_core TEXT;
EXCEPTION WHEN duplicate_column THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE choices ADD COLUMN keywords JSONB DEFAULT '[]';
EXCEPTION WHEN duplicate_column THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE choices ADD COLUMN explanation TEXT;
EXCEPTION WHEN duplicate_column THEN NULL; END $$;
