"""
llm_processor.py — Transform MCQ questions into O/X flashcards via Claude API with RAG.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import anthropic
from dotenv import load_dotenv
from pydantic import ValidationError

from .models import (
    ImportanceGrade,
    OX_LETTERS,
    OXStatement,
    RawQuestion,
    Source,
    TransformedQuestion,
)
from .legal_retriever import LegalRetriever

# Load env from backend
load_dotenv(Path(__file__).resolve().parents[1] / "backend" / ".env")

log = logging.getLogger(__name__)

MODEL        = "claude-3-sonnet-20240229"
MAX_TOKENS   = 4096
CONCURRENCY  = 3
RETRY_LIMIT  = 5
RETRY_DELAY  = 2.0

# ── Tool schema ──────────────────────────────────────────────────────────────

OX_TOOL = {
    "name": "submit_ox_analysis",
    "description": "Submit a structured O/X analysis for a Korean bar exam MCQ question.",
    "input_schema": {
        "type": "object",
        "required": ["overall_explanation", "ox_statements"],
        "properties": {
            "overall_explanation": {"type": "string"},
            "ox_statements": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": [
                        "letter", "choice_number", "statement",
                        "is_correct", "importance", "explanation",
                        "explanation_core", "keywords"
                    ],
                    "properties": {
                        "letter": {"type": "string", "enum": OX_LETTERS},
                        "choice_number": {"type": "integer"},
                        "statement": {"type": "string"},
                        "is_correct": {"type": "boolean"},
                        "legal_basis": {"type": "string"},
                        "case_citation": {"type": "string"},
                        "explanation_core": {"type": "string"},
                        "keywords": {"type": "array", "items": {"type": "string"}},
                        "theory": {"type": "string"},
                        "is_revised": {"type": "boolean"},
                        "revision_note": {"type": "string"},
                        "importance": {"type": "string", "enum": ["A", "B", "C"]},
                        "explanation": {"type": "string"},
                    },
                },
            },
        },
    },
}

def _build_prompt(q: RawQuestion, retrieved_context: Optional[str] = None) -> str:
    choices_text = "
".join(
        f"  {OX_LETTERS[i-1]}({i}) {text}"
        for i, text in sorted(q.choices.items())
    )
    context_section = ""
    if retrieved_context:
        context_section = f"
[RAG Context - Real Legal Texts]
{retrieved_context}
"

    return f"""Analyze this Korean Bar Exam Question.
Context: {context_section}
Question: {q.stem}
Choices:
{choices_text}
Correct Answer: {q.correct_choice}

Instructions:
1. Split into 5 independent O/X statements.
2. Use the provided RAG Context to verify facts.
3. Fill all fields (legal_basis, case_citation, etc.).
"""

class MCQTransformer:
    def __init__(self, api_key: Optional[str] = None, concurrency: int = CONCURRENCY):
        key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        self._client = anthropic.AsyncClient(api_key=key)
        self._http_client = httpx.AsyncClient(timeout=10.0)
        self._retriever = LegalRetriever(self._http_client)
        self._semaphore = asyncio.Semaphore(concurrency)

    async def transform_question(self, q: RawQuestion) -> Optional[TransformedQuestion]:
        async with self._semaphore:
            # 1. RAG Retrieval
            retrieved_context = ""
            case_numbers = re.findall(r"\d{4}[가-힣]+\d+", q.stem + " " + " ".join(q.choices.values()))
            if case_numbers:
                # Fetch first case for demo
                text = await self._retriever.fetch_precedent(case_numbers[0])
                if text:
                    retrieved_context += f"Case {case_numbers[0]}: {text}
"

            # 2. LLM Generation
            prompt = _build_prompt(q, retrieved_context)
            try:
                response = await self._client.messages.create(
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    tools=[OX_TOOL],
                    tool_choice={"type": "tool", "name": "submit_ox_analysis"},
                    messages=[{"role": "user", "content": prompt}],
                )
                
                tool_block = next((b for b in response.content if b.type == "tool_use"), None)
                if not tool_block:
                    return None
                
                raw = tool_block.input
                ox_list = []
                for i, stmt in enumerate(raw.get("ox_statements", [])):
                    ox_list.append(OXStatement(
                        letter=stmt.get("letter", OX_LETTERS[i]),
                        choice_number=stmt.get("choice_number", i + 1),
                        statement=stmt["statement"],
                        is_correct=bool(stmt["is_correct"]),
                        legal_basis=stmt.get("legal_basis"),
                        case_citation=stmt.get("case_citation"),
                        explanation_core=stmt.get("explanation_core"),
                        keywords=stmt.get("keywords", []),
                        theory=stmt.get("theory"),
                        is_revised=bool(stmt.get("is_revised", False)),
                        revision_note=stmt.get("revision_note"),
                        importance=ImportanceGrade(stmt.get("importance", "B")),
                        explanation=stmt["explanation"],
                    ))
                
                return TransformedQuestion(
                    source=q.source,
                    raw_id=q.raw_id,
                    exam_session=q.exam_session,
                    year=q.year,
                    month=q.month,
                    subject=q.subject,
                    question_number=q.question_number,
                    stem=q.stem,
                    choices=q.choices,
                    correct_choice=q.correct_choice,
                    tags=q.tags,
                    is_outdated=q.is_outdated,
                    needs_revision=q.needs_revision,
                    overall_explanation=raw.get("overall_explanation"),
                    ox_statements=ox_list,
                )
            except Exception as e:
                log.error(f"Error transforming {q.raw_id}: {e}")
                return None

    async def transform_batch(self, questions: List[RawQuestion], checkpoint_path: Optional[Path] = None) -> List[TransformedQuestion]:
        results = []
        for q in questions:
            res = await self.transform_question(q)
            if res:
                results.append(res)
        return results
