"""SQLAlchemy 2.0 ORM models — Python 3.8 compatible."""
from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


# ── Users ─────────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email:         Mapped[str]           = mapped_column(String, unique=True, nullable=False)
    password_hash: Mapped[str]           = mapped_column(String, nullable=False)
    display_name:  Mapped[Optional[str]] = mapped_column(String)

    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    study_streak:      Mapped[int]       = mapped_column(Integer, default=0, nullable=False)
    longest_streak:    Mapped[int]       = mapped_column(Integer, default=0, nullable=False)
    last_studied_date: Mapped[Optional[date]] = mapped_column(Date)

    vacation_mode_enabled: Mapped[bool]             = mapped_column(Boolean, default=False, nullable=False)
    vacation_started_at:   Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    sm2_hard_interval_minutes: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    sm2_good_interval_days:    Mapped[int] = mapped_column(Integer, default=1,  nullable=False)
    sm2_easy_interval_days:    Mapped[int] = mapped_column(Integer, default=3,  nullable=False)

    daily_new_limit:    Mapped[int]   = mapped_column(Integer,      default=20,    nullable=False)
    daily_review_limit: Mapped[int]   = mapped_column(Integer,      default=200,   nullable=False)
    target_retention:   Mapped[float] = mapped_column(Numeric(4, 3), default=0.900, nullable=False)
    learning_steps:     Mapped[str]   = mapped_column(String(50),   default='1 10', nullable=False)
    relearning_steps:   Mapped[str]   = mapped_column(String(50),   default='10',   nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    progress:       Mapped[List["UserProgress"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    review_logs:    Mapped[List["ReviewLog"]]    = relationship(back_populates="user", cascade="all, delete-orphan")
    study_sessions: Mapped[List["StudySession"]] = relationship(back_populates="user", cascade="all, delete-orphan")


# ── Subjects ──────────────────────────────────────────────────────────────────

class Subject(Base):
    __tablename__ = "subjects"

    id:          Mapped[uuid.UUID]   = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name:        Mapped[str]         = mapped_column(String, unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    sort_order:  Mapped[int]         = mapped_column(Integer, default=0, nullable=False)
    created_at:  Mapped[datetime]    = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    questions: Mapped[List["Question"]] = relationship(back_populates="subject")


# ── Tags ──────────────────────────────────────────────────────────────────────

class Tag(Base):
    __tablename__ = "tags"

    id:         Mapped[uuid.UUID]   = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name:       Mapped[str]         = mapped_column(String, unique=True, nullable=False)
    color_hex:  Mapped[Optional[str]] = mapped_column(String(7))
    created_at: Mapped[datetime]    = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    question_tags: Mapped[List["QuestionTag"]] = relationship(back_populates="tag", cascade="all, delete-orphan")


# ── Questions ─────────────────────────────────────────────────────────────────

class Question(Base):
    __tablename__ = "questions"

    id:         Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subjects.id", ondelete="RESTRICT"), nullable=False
    )

    exam_type:       Mapped[str]          = mapped_column(String, nullable=False, default="Korean Bar Exam")
    source_year:     Mapped[Optional[int]] = mapped_column(SmallInteger)
    source_name:     Mapped[Optional[str]] = mapped_column(String)
    question_number: Mapped[Optional[int]] = mapped_column(SmallInteger)

    stem:           Mapped[str]           = mapped_column(Text, nullable=False)
    correct_choice: Mapped[int]           = mapped_column(SmallInteger, nullable=False)
    explanation:    Mapped[Optional[str]] = mapped_column(Text)

    tags: Mapped[List[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default="{}"
    )

    is_outdated:     Mapped[bool]           = mapped_column(Boolean, default=False, nullable=False)
    needs_revision:  Mapped[bool]           = mapped_column(Boolean, default=False, nullable=False)
    outdated_reason: Mapped[Optional[str]]  = mapped_column(Text)

    total_attempts:   Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    correct_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    keywords: Mapped[Optional[List[str]]] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    subject:       Mapped["Subject"]           = relationship(back_populates="questions")
    choices:       Mapped[List["Choice"]]      = relationship(back_populates="question", cascade="all, delete-orphan", order_by="Choice.choice_number")
    flashcards:    Mapped[List["Flashcard"]]   = relationship(back_populates="question", cascade="all, delete-orphan")
    question_tags: Mapped[List["QuestionTag"]] = relationship(back_populates="question", cascade="all, delete-orphan")


# ── QuestionTags ──────────────────────────────────────────────────────────────

class QuestionTag(Base):
    __tablename__ = "question_tags"

    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("questions.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )

    question: Mapped["Question"] = relationship(back_populates="question_tags")
    tag:      Mapped["Tag"]      = relationship(back_populates="question_tags")


# ── Choices ───────────────────────────────────────────────────────────────────

class Choice(Base):
    __tablename__ = "choices"

    id:            Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    question_id:   Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("questions.id", ondelete="CASCADE"), nullable=False
    )
    choice_number: Mapped[int]  = mapped_column(SmallInteger, nullable=False)
    content:       Mapped[str]  = mapped_column(Text, nullable=False)
    is_correct:    Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    legal_basis: Mapped[Optional[str]] = mapped_column(String)
    case_citation: Mapped[Optional[str]] = mapped_column(String)
    explanation_core: Mapped[Optional[str]] = mapped_column(Text)

    __table_args__ = (

    question:   Mapped["Question"]         = relationship(back_populates="choices")
    flashcards: Mapped[List["Flashcard"]]  = relationship(back_populates="choice")


# ── Flashcards ────────────────────────────────────────────────────────────────

class Flashcard(Base):
    __tablename__ = "flashcards"

    id:          Mapped[uuid.UUID]        = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    question_id: Mapped[uuid.UUID]        = mapped_column(
        UUID(as_uuid=True), ForeignKey("questions.id", ondelete="CASCADE"), nullable=False
    )
    choice_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("choices.id", ondelete="CASCADE")
    )
    type: Mapped[str] = mapped_column(String, nullable=False, default="question")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("question_id", "type", "choice_id"),
    )

    question:    Mapped["Question"]           = relationship(back_populates="flashcards")
    choice:      Mapped[Optional["Choice"]]   = relationship(back_populates="flashcards")
    progress:    Mapped[List["UserProgress"]] = relationship(back_populates="flashcard", cascade="all, delete-orphan")
    review_logs: Mapped[List["ReviewLog"]]    = relationship(back_populates="flashcard", cascade="all, delete-orphan")


# ── UserProgress ──────────────────────────────────────────────────────────────

class UserProgress(Base):
    __tablename__ = "user_progress"

    id:           Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id:      Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    flashcard_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("flashcards.id", ondelete="CASCADE"), nullable=False
    )

    ease_factor:      Mapped[float]            = mapped_column(Numeric(4, 2),  default=2.50, nullable=False)
    interval_days:    Mapped[float]            = mapped_column(Numeric(10, 4), default=0,    nullable=False)
    repetitions:      Mapped[int]              = mapped_column(Integer,         default=0,   nullable=False)
    next_review_at:   Mapped[datetime]         = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_rating:      Mapped[Optional[int]]    = mapped_column(SmallInteger)

    personal_note: Mapped[Optional[str]] = mapped_column(Text)
    is_starred:    Mapped[bool]          = mapped_column(Boolean, default=False, nullable=False)

    card_state:         Mapped[str]              = mapped_column(String(20), default='new', nullable=False)
    learning_step:      Mapped[int]              = mapped_column(SmallInteger, default=0, nullable=False)
    learning_due_at:    Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    lapses:             Mapped[int]              = mapped_column(Integer, default=0, nullable=False)
    date_first_studied: Mapped[Optional[date]]   = mapped_column(Date)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("user_id", "flashcard_id"),
    )

    user:      Mapped["User"]      = relationship(back_populates="progress")
    flashcard: Mapped["Flashcard"] = relationship(back_populates="progress")


# ── ReviewLog ─────────────────────────────────────────────────────────────────

class ReviewLog(Base):
    __tablename__ = "review_logs"

    id:           Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id:      Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    flashcard_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("flashcards.id", ondelete="CASCADE"), nullable=False
    )

    rating:        Mapped[int]           = mapped_column(SmallInteger, nullable=False)
    answer_given:  Mapped[Optional[int]] = mapped_column(SmallInteger)
    was_correct:   Mapped[bool]          = mapped_column(Boolean, nullable=False)
    time_spent_ms: Mapped[Optional[int]] = mapped_column(Integer)

    prev_ease_factor:   Mapped[Optional[float]]    = mapped_column(Numeric(4, 2))
    prev_interval_days: Mapped[Optional[float]]    = mapped_column(Numeric(10, 4))
    prev_repetitions:   Mapped[Optional[int]]      = mapped_column(Integer)
    prev_card_state:    Mapped[Optional[str]]      = mapped_column(String(20))

    new_ease_factor:    Mapped[Optional[float]]    = mapped_column(Numeric(4, 2))
    new_interval_days:  Mapped[Optional[float]]    = mapped_column(Numeric(10, 4))
    new_next_review_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user:      Mapped["User"]      = relationship(back_populates="review_logs")
    flashcard: Mapped["Flashcard"] = relationship(back_populates="review_logs")


# ── StudySession ──────────────────────────────────────────────────────────────

class StudySession(Base):
    __tablename__ = "study_sessions"

    id:             Mapped[uuid.UUID]  = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id:        Mapped[uuid.UUID]  = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    session_date:   Mapped[date]       = mapped_column(Date, nullable=False)
    cards_reviewed: Mapped[int]        = mapped_column(Integer, default=0, nullable=False)
    correct_count:  Mapped[int]        = mapped_column(Integer, default=0, nullable=False)
    duration_ms:    Mapped[Optional[int]] = mapped_column(Integer)
    created_at:     Mapped[datetime]   = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("user_id", "session_date"),
    )

    user: Mapped["User"] = relationship(back_populates="study_sessions")
