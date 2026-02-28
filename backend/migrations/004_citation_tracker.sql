-- 004_citation_tracker.sql
-- Adds citation-verification tracking columns to questions.
-- Run once: psql $DATABASE_URL -f backend/migrations/004_citation_tracker.sql

ALTER TABLE questions
    ADD COLUMN IF NOT EXISTS last_citation_check_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS citation_check_status   TEXT DEFAULT 'unchecked'
        CHECK (citation_check_status IN ('unchecked', 'ok', 'needs_review', 'error'));

-- Also add the full-text explanation column to choices (if not added in 003)
ALTER TABLE choices
    ADD COLUMN IF NOT EXISTS explanation TEXT;

-- Indexes for the scheduler query
CREATE INDEX IF NOT EXISTS idx_questions_citation_check_at
    ON questions (last_citation_check_at NULLS FIRST);

CREATE INDEX IF NOT EXISTS idx_questions_citation_status
    ON questions (citation_check_status)
    WHERE citation_check_status = 'needs_review';
