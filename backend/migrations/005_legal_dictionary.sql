-- Migration 005: Legal Dictionary tables (law_statutes + legal_precedents)

CREATE TABLE IF NOT EXISTS law_statutes (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    law_id         TEXT UNIQUE NOT NULL,
    name           TEXT NOT NULL,
    category       TEXT,
    subject        TEXT,
    effective_date TEXT,
    law_url        TEXT,
    created_at     TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS legal_precedents (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_number     TEXT UNIQUE NOT NULL,
    case_name       TEXT,
    court           TEXT DEFAULT '대법원',
    decision_date   TEXT,
    verdict_summary TEXT,
    holding         TEXT,
    serial_number   TEXT,
    subject         TEXT,
    source_url      TEXT,
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_law_statutes_name
    ON law_statutes USING gin(to_tsvector('simple', name));

CREATE INDEX IF NOT EXISTS idx_legal_precedents_search
    ON legal_precedents USING gin(
        to_tsvector('simple',
            coalesce(case_number, '') || ' ' ||
            coalesce(case_name, '')   || ' ' ||
            coalesce(holding, '')
        )
    );
