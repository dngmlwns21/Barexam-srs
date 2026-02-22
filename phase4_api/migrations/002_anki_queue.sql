-- 002_anki_queue.sql — Phase 4: Anki card state machine + SRS settings
-- Run against Neon PostgreSQL:
--   psql $DATABASE_URL -f phase4_api/migrations/002_anki_queue.sql

-- ── user_progress: card state machine columns ─────────────────────────────────
ALTER TABLE user_progress
  ADD COLUMN IF NOT EXISTS card_state         VARCHAR(20)  NOT NULL DEFAULT 'new',
  ADD COLUMN IF NOT EXISTS learning_step      SMALLINT     NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS learning_due_at    TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS lapses             INTEGER      NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS date_first_studied DATE;

-- Backfill: already-reviewed cards → 'review', dated in the distant past
-- so they do NOT count against today's daily new-card limit.
UPDATE user_progress
SET   card_state         = 'review',
      date_first_studied = '2020-01-01'
WHERE repetitions > 0 OR last_reviewed_at IS NOT NULL;

-- ── users: SRS settings ───────────────────────────────────────────────────────
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS daily_new_limit    INTEGER      NOT NULL DEFAULT 20,
  ADD COLUMN IF NOT EXISTS daily_review_limit INTEGER      NOT NULL DEFAULT 200,
  ADD COLUMN IF NOT EXISTS target_retention   NUMERIC(4,3) NOT NULL DEFAULT 0.900,
  ADD COLUMN IF NOT EXISTS learning_steps     VARCHAR(50)  NOT NULL DEFAULT '1 10',
  ADD COLUMN IF NOT EXISTS relearning_steps   VARCHAR(50)  NOT NULL DEFAULT '10';

-- ── review_logs: track state at review time ───────────────────────────────────
ALTER TABLE review_logs
  ADD COLUMN IF NOT EXISTS prev_card_state VARCHAR(20);
