-- Phase 4 — Initial Schema Migration
-- Run against your Supabase (or any PostgreSQL ≥ 14) database.
-- Safe to re-run: all statements use IF NOT EXISTS / OR REPLACE.

-- ============================================================
-- EXTENSIONS
-- ============================================================
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";
CREATE EXTENSION IF NOT EXISTS "btree_gin";

-- ============================================================
-- USERS
-- ============================================================
CREATE TABLE IF NOT EXISTS users (
    id                          UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    email                       TEXT        UNIQUE NOT NULL,
    password_hash               TEXT        NOT NULL,
    display_name                TEXT,
    last_synced_at              TIMESTAMPTZ,
    study_streak                INTEGER     NOT NULL DEFAULT 0,
    longest_streak              INTEGER     NOT NULL DEFAULT 0,
    last_studied_date           DATE,
    vacation_mode_enabled       BOOLEAN     NOT NULL DEFAULT FALSE,
    vacation_started_at         TIMESTAMPTZ,
    sm2_hard_interval_minutes   INTEGER     NOT NULL DEFAULT 10,
    sm2_good_interval_days      INTEGER     NOT NULL DEFAULT 1,
    sm2_easy_interval_days      INTEGER     NOT NULL DEFAULT 3,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- SUBJECTS
-- ============================================================
CREATE TABLE IF NOT EXISTS subjects (
    id          UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    name        TEXT        UNIQUE NOT NULL,
    description TEXT,
    sort_order  INTEGER     NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- TAGS
-- ============================================================
CREATE TABLE IF NOT EXISTS tags (
    id          UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    name        TEXT        UNIQUE NOT NULL,
    color_hex   TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- QUESTIONS
-- ============================================================
CREATE TABLE IF NOT EXISTS questions (
    id               UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    subject_id       UUID        NOT NULL REFERENCES subjects(id) ON DELETE RESTRICT,
    exam_type        TEXT        NOT NULL DEFAULT 'Korean Bar Exam',
    source_year      SMALLINT,
    source_name      TEXT,
    question_number  SMALLINT,
    stem             TEXT        NOT NULL,
    correct_choice   SMALLINT    NOT NULL CHECK (correct_choice BETWEEN 1 AND 5),
    explanation      TEXT,
    tags             TEXT[]      NOT NULL DEFAULT '{}',
    is_outdated      BOOLEAN     NOT NULL DEFAULT FALSE,
    needs_revision   BOOLEAN     NOT NULL DEFAULT FALSE,
    outdated_reason  TEXT,
    total_attempts   INTEGER     NOT NULL DEFAULT 0,
    correct_attempts INTEGER     NOT NULL DEFAULT 0,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- QUESTION_TAGS
-- ============================================================
CREATE TABLE IF NOT EXISTS question_tags (
    question_id UUID NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    tag_id      UUID NOT NULL REFERENCES tags(id)      ON DELETE CASCADE,
    PRIMARY KEY (question_id, tag_id)
);

-- ============================================================
-- CHOICES
-- ============================================================
CREATE TABLE IF NOT EXISTS choices (
    id              UUID     PRIMARY KEY DEFAULT uuid_generate_v4(),
    question_id     UUID     NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    choice_number   SMALLINT NOT NULL CHECK (choice_number BETWEEN 1 AND 5),
    content         TEXT     NOT NULL,
    is_correct      BOOLEAN  NOT NULL DEFAULT FALSE,
    UNIQUE(question_id, choice_number)
);

-- ============================================================
-- FLASHCARDS
-- ============================================================
CREATE TABLE IF NOT EXISTS flashcards (
    id           UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    question_id  UUID        NOT NULL REFERENCES questions(id)  ON DELETE CASCADE,
    choice_id    UUID                 REFERENCES choices(id)    ON DELETE CASCADE,
    type         TEXT        NOT NULL DEFAULT 'question'
                             CHECK (type IN ('question','choice_ox')),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_choice_ox_requires_choice
        CHECK (type = 'question' OR choice_id IS NOT NULL),
    UNIQUE(question_id, type, choice_id)
);

-- ============================================================
-- USER_PROGRESS
-- ============================================================
CREATE TABLE IF NOT EXISTS user_progress (
    id               UUID          PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id          UUID          NOT NULL REFERENCES users(id)      ON DELETE CASCADE,
    flashcard_id     UUID          NOT NULL REFERENCES flashcards(id) ON DELETE CASCADE,
    ease_factor      NUMERIC(4,2)  NOT NULL DEFAULT 2.50,
    interval_days    NUMERIC(10,4) NOT NULL DEFAULT 0,
    repetitions      INTEGER       NOT NULL DEFAULT 0,
    next_review_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    last_reviewed_at TIMESTAMPTZ,
    last_rating      SMALLINT,
    personal_note    TEXT,
    is_starred       BOOLEAN       NOT NULL DEFAULT FALSE,
    created_at       TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    UNIQUE(user_id, flashcard_id)
);

-- ============================================================
-- REVIEW_LOGS
-- ============================================================
CREATE TABLE IF NOT EXISTS review_logs (
    id                  UUID          PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id             UUID          NOT NULL REFERENCES users(id)      ON DELETE CASCADE,
    flashcard_id        UUID          NOT NULL REFERENCES flashcards(id) ON DELETE CASCADE,
    rating              SMALLINT      NOT NULL CHECK (rating BETWEEN 0 AND 5),
    answer_given        SMALLINT,
    was_correct         BOOLEAN       NOT NULL,
    time_spent_ms       INTEGER,
    prev_ease_factor    NUMERIC(4,2),
    prev_interval_days  NUMERIC(10,4),
    prev_repetitions    INTEGER,
    new_ease_factor     NUMERIC(4,2),
    new_interval_days   NUMERIC(10,4),
    new_next_review_at  TIMESTAMPTZ,
    reviewed_at         TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

-- ============================================================
-- STUDY_SESSIONS
-- ============================================================
CREATE TABLE IF NOT EXISTS study_sessions (
    id              UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_date    DATE        NOT NULL,
    cards_reviewed  INTEGER     NOT NULL DEFAULT 0,
    correct_count   INTEGER     NOT NULL DEFAULT 0,
    duration_ms     INTEGER,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(user_id, session_date)
);

-- ============================================================
-- INDEXES
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_q_subject       ON questions(subject_id);
CREATE INDEX IF NOT EXISTS idx_q_exam_type     ON questions(exam_type);
CREATE INDEX IF NOT EXISTS idx_q_source_year   ON questions(source_year);
CREATE INDEX IF NOT EXISTS idx_q_tags          ON questions USING GIN(tags);
CREATE INDEX IF NOT EXISTS idx_q_outdated      ON questions(is_outdated) WHERE is_outdated = TRUE;
CREATE INDEX IF NOT EXISTS idx_choices_qid     ON choices(question_id);
CREATE INDEX IF NOT EXISTS idx_fc_question     ON flashcards(question_id);
CREATE INDEX IF NOT EXISTS idx_fc_choice       ON flashcards(choice_id);
CREATE INDEX IF NOT EXISTS idx_up_due          ON user_progress(user_id, next_review_at ASC)
    WHERE is_starred = FALSE;
CREATE INDEX IF NOT EXISTS idx_up_starred      ON user_progress(user_id)
    WHERE is_starred = TRUE;
CREATE INDEX IF NOT EXISTS idx_rl_user_time    ON review_logs(user_id, reviewed_at DESC);
CREATE INDEX IF NOT EXISTS idx_ss_user_date    ON study_sessions(user_id, session_date DESC);
