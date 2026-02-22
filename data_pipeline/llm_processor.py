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
    choices_text = "\n".join(
        f"  {OX_LETTERS[i-1]}({i}) {text}"
        for i, text in sorted(q.choices.items())
    )
    
    context_block = ""
    if retrieved_context:
        context_block = f"""
**CRITICAL: You MUST use the following retrieved legal text to ground your analysis. Do not use outside knowledge if this context is provided.**
[RAG Context - Real Legal Texts]
{retrieved_context}
[End of RAG Context]
"""

    return f"""You are an expert legal analyst. Your task is to deconstruct a Korean Bar Exam multiple-choice question into five separate True/False (O/X) statements and provide a detailed, structured analysis for each.

{context_block}

**Source Question Analysis:**
*   **Question Stem:** {q.stem}
*   **Choices:**
{choices_text}
*   **Correct Answer:** Choice {q.correct_choice}

**Your Task:**
1.  **Deconstruct:** Analyze each choice ({", ".join(OX_LETTERS[:len(q.choices)])}) and rephrase it as a standalone O/X statement.
2.  **Verify:** Determine if each statement is True (O) or False (X).
3.  **Justify:** For each statement, provide a rigorous legal justification.
    *   If RAG Context is available, you **MUST** base your justification on it, citing specific parts.
    *   Identify the core legal principle (`explanation_core`).
    *   Cite the specific legal statute (`legal_basis`) or court case (`case_citation`) if applicable.
    *   Extract 3-5 relevant `keywords`.
4.  **Grade Importance:** Assign an importance grade (A, B, C) based on how fundamental the legal principle is.
5.  **Submit:** Use the `submit_ox_analysis` tool with the complete, structured data for all statements.
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
                    retrieved_context += f"Case {case_numbers[0]}: {text}\n"

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
