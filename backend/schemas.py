"""Pydantic v2 request/response schemas."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, EmailStr, Field


# ── Auth ──────────────────────────────────────────────────────────────────────

class RegisterIn(BaseModel):
    email:        EmailStr
    password:     str = Field(min_length=8)
    display_name: Optional[str] = None


class LoginIn(BaseModel):
    email:    EmailStr
    password: str


class TokenOut(BaseModel):
    access_token:  str
    refresh_token: str
    token_type:    str = "bearer"


class AccessTokenOut(BaseModel):
    access_token: str
    token_type:   str = "bearer"


# ── User ──────────────────────────────────────────────────────────────────────

class UserOut(BaseModel):
    id:           uuid.UUID
    email:        str
    display_name: Optional[str]

    study_streak:          int
    longest_streak:        int
    last_studied_date:     Optional[date]
    vacation_mode_enabled: bool
    vacation_started_at:   Optional[datetime]

    sm2_hard_interval_minutes: int
    sm2_good_interval_days:    int
    sm2_easy_interval_days:    int

    daily_new_limit:    int
    daily_review_limit: int
    target_retention:   float
    learning_steps:     str
    relearning_steps:   str

    last_synced_at: Optional[datetime]
    created_at:     datetime

    model_config = {"from_attributes": True}


class StudySettingsIn(BaseModel):
    daily_new_limit:    Optional[int]   = Field(None, ge=0, le=500)
    daily_review_limit: Optional[int]   = Field(None, ge=0, le=9999)
    target_retention:   Optional[float] = Field(None, ge=0.50, le=0.99)
    learning_steps:     Optional[str]   = None
    relearning_steps:   Optional[str]   = None


class UserUpdateIn(BaseModel):
    display_name:              Optional[str] = None
    sm2_hard_interval_minutes: Optional[int] = Field(None, ge=1, le=1440)
    sm2_good_interval_days:    Optional[int] = Field(None, ge=1, le=365)
    sm2_easy_interval_days:    Optional[int] = Field(None, ge=1, le=365)


class VacationIn(BaseModel):
    enabled: bool


class StreakOut(BaseModel):
    study_streak:      int
    longest_streak:    int
    last_studied_date: Optional[date]


class HeatmapEntry(BaseModel):
    date:           date
    cards_reviewed: int
    correct_count:  int


# ── Subject ───────────────────────────────────────────────────────────────────

class SubjectOut(BaseModel):
    id:              uuid.UUID
    name:            str
    description:     Optional[str]
    total_questions: int = 0
    due_count:       int = 0

    model_config = {"from_attributes": True}


# ── Tag ───────────────────────────────────────────────────────────────────────

class TagOut(BaseModel):
    id:        uuid.UUID
    name:      str
    color_hex: Optional[str]
    usage:     int = 0

    model_config = {"from_attributes": True}


# ── Choice ────────────────────────────────────────────────────────────────────

class ChoicePublicOut(BaseModel):
    """Choice data sent to client before answer reveal — is_correct is omitted."""
    id:            uuid.UUID
    choice_number: int
    content:       str

    model_config = {"from_attributes": True}


class ChoiceOut(BaseModel):
    id:            uuid.UUID
    choice_number: int
    content:       str
    is_correct:    bool
    legal_basis:      Optional[str]       = None
    case_citation:    Optional[str]       = None
    explanation_core: Optional[str]       = None   # core_reasoning (one sentence)
    explanation:      Optional[str]       = None   # detailed_explanation (step-by-step ①②③)
    keywords:         Optional[List[str]] = None

    model_config = {"from_attributes": True}


# ── Question ──────────────────────────────────────────────────────────────────

class QuestionCardOut(BaseModel):
    """Question shape used inside DueCardOut — MCQ choices have is_correct stripped."""
    id:              uuid.UUID
    subject_id:      uuid.UUID
    exam_type:       str
    source_year:     Optional[int]
    source_name:     Optional[str]
    question_number: Optional[int]
    stem:            str
    correct_choice:  int        # kept for immediate client-side reveal
    explanation:     Optional[str]
    tags:            List[str]
    is_outdated:     bool
    needs_revision:  bool
    outdated_reason: Optional[str]
    keywords:        Optional[List[str]] = None # Existing field, ensure it's here
    overall_explanation: Optional[str] = None # New field
    choices:         List[ChoicePublicOut] = []   # is_correct stripped
    created_at:      datetime

    model_config = {"from_attributes": True}


class QuestionOut(BaseModel):
    id:              uuid.UUID
    subject_id:      uuid.UUID
    exam_type:       str
    source_year:     Optional[int]
    source_name:     Optional[str]
    question_number: Optional[int]
    stem:            str
    correct_choice:  int
    explanation:     Optional[str]
    tags:            List[str]
    is_outdated:     bool
    needs_revision:  bool
    outdated_reason: Optional[str]
    keywords:        Optional[List[str]] = None
    overall_explanation: Optional[str] = None # New field
    choices:         List[ChoiceOut] = []
    created_at:      datetime

    model_config = {"from_attributes": True}


class QuestionListOut(BaseModel):
    id:              uuid.UUID
    subject_id:      uuid.UUID
    exam_type:       str
    source_year:     Optional[int]
    source_name:     Optional[str]
    question_number: Optional[int]
    stem:            str
    tags:            List[str]
    is_outdated:     bool
    needs_revision:  bool
    total_attempts:  int
    correct_attempts: int

    model_config = {"from_attributes": True}


class AnswerOut(BaseModel):
    answer:      int                # correct_choice (1-5)
    explanation: Optional[str]


class QuestionStatsOut(BaseModel):
    question_id:      uuid.UUID
    total_attempts:   int
    correct_attempts: int
    difficulty_pct:   float         # 0-100, lower = harder


class NoteIn(BaseModel):
    personal_note: Optional[str] = None


class StarIn(BaseModel):
    is_starred: bool


class SetTagsIn(BaseModel):
    tag_ids: List[uuid.UUID]


class BookmarkedQuestionOut(BaseModel):
    id: uuid.UUID
    flashcard_id: uuid.UUID
    stem: str
    subject_name: str
    tags: List[str]
    is_starred: bool
    personal_note: Optional[str]


# ── Flashcard ─────────────────────────────────────────────────────────────────

class SM2StateOut(BaseModel):
    ease_factor:    float
    interval_days:  float
    repetitions:    int
    next_review_at: datetime
    last_reviewed_at: Optional[datetime]
    last_rating:    Optional[int]


class DueCardOut(BaseModel):
    flashcard_id:  uuid.UUID
    type:          str                   # "question" | "choice_ox"
    question:      QuestionCardOut       # MCQ choices have is_correct stripped
    choice:        Optional[ChoiceOut]   # kept for OX reveal (needs is_correct)
    sm2:           SM2StateOut
    personal_note: Optional[str]
    is_starred:    bool


# ── Review ────────────────────────────────────────────────────────────────────

class ReviewIn(BaseModel):
    rating:        int  = Field(ge=0, le=5)
    answer_given:  Optional[int] = Field(None, ge=1, le=5)
    time_spent_ms: Optional[int] = Field(None, ge=0)


class ReviewOut(BaseModel):
    flashcard_id:   uuid.UUID
    was_correct:    bool
    new_sm2:        SM2StateOut
    peer_stats:     Optional[QuestionStatsOut] = None


class ReviewLogOut(BaseModel):
    id:           uuid.UUID
    flashcard_id: uuid.UUID
    rating:       int
    was_correct:  bool
    reviewed_at:  datetime
    question_stem: Optional[str] = None
    card_type:     Optional[str] = None
    subject_name:  Optional[str] = None

    model_config = {"from_attributes": True}


class WeeklyDayOut(BaseModel):
    date:     str    # YYYY-MM-DD
    reviewed: int
    correct:  int
    accuracy: float


class WeeklyStatsOut(BaseModel):
    days: List["WeeklyDayOut"]


# ── Stats ─────────────────────────────────────────────────────────────────────

class OverallStatsOut(BaseModel):
    total_cards:     int
    due_today:       int
    reviewed_today:  int
    correct_today:   int
    accuracy_7d:     float
    study_streak:    int


class SubjectStatsOut(BaseModel):
    subject_id:   uuid.UUID
    subject_name: str
    total:        int
    due:          int
    reviewed_today: int
    accuracy_all: float


# ── Dashboard ─────────────────────────────────────────────────────────────────

class DailyStatusOut(BaseModel):
    done: List[DueCardOut]   # reviewed today
    todo: List[DueCardOut]   # due but not yet reviewed today


class DeckStatsOut(BaseModel):
    subject_id:     Optional[uuid.UUID]
    subject_name:   str
    new_count:      int   # blue  — new cards available today
    learning_count: int   # red   — learning/lapsed due now
    review_count:   int   # green — review cards due today
    total_cards:    int


# ── Pagination ────────────────────────────────────────────────────────────────

class PaginatedQuestions(BaseModel):
    items:   List[QuestionListOut]
    total:   int
    page:    int
    limit:   int
    pages:   int
