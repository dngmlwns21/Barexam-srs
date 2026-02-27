"""
llm_processor.py — Transform MCQ questions into O/X flashcards via Vertex AI Gemini with RAG.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from dotenv import load_dotenv
from pydantic import ValidationError

import vertexai
from vertexai.generative_models import (
    FunctionDeclaration,
    GenerationConfig,
    GenerativeModel,
    Tool,
    ToolConfig,
)

from .models import (
    ImportanceGrade,
    OX_LETTERS,
    OXStatement,
    RawQuestion,
    TransformedQuestion,
)
from .legal_retriever import LegalRetriever

load_dotenv(Path(__file__).resolve().parents[1] / "backend" / ".env")

log = logging.getLogger(__name__)

MODEL       = "gemini-2.0-flash"
MAX_TOKENS  = 4096
CONCURRENCY = 1      # free-tier rate limit: 1 concurrent request
RETRY_LIMIT = 5
RETRY_DELAY = 15.0   # base wait on 429 (doubles each retry: 15s, 30s, 60s…)

# ── Tool schema ───────────────────────────────────────────────────────────────

_OX_FUNCTION = FunctionDeclaration(
    name="submit_ox_analysis",
    description="Submit a structured O/X analysis for a Korean bar exam MCQ question.",
    parameters={
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
                        "explanation_core", "keywords",
                    ],
                    "properties": {
                        "letter":           {"type": "string"},
                        "choice_number":    {"type": "integer"},
                        "statement":        {"type": "string"},
                        "is_correct":       {"type": "boolean"},
                        "legal_basis":      {"type": "string"},
                        "case_citation":    {"type": "string"},
                        "explanation_core": {"type": "string"},
                        "keywords":         {"type": "array", "items": {"type": "string"}},
                        "theory":           {"type": "string"},
                        "is_revised":       {"type": "boolean"},
                        "revision_note":    {"type": "string"},
                        "importance":       {"type": "string"},
                        "explanation":      {"type": "string"},
                    },
                },
            },
        },
    },
)

_OX_TOOL = Tool(function_declarations=[_OX_FUNCTION])
_TOOL_CONFIG = ToolConfig(
    function_calling_config=ToolConfig.FunctionCallingConfig(
        mode=ToolConfig.FunctionCallingConfig.Mode.ANY,
        allowed_function_names=["submit_ox_analysis"],
    )
)


def _proto_to_py(val: Any) -> Any:
    """Recursively convert Vertex AI proto MapComposite/ListComposite to plain Python."""
    if hasattr(val, "items"):
        return {k: _proto_to_py(v) for k, v in val.items()}
    if hasattr(val, "__iter__") and not isinstance(val, (str, bytes)):
        return [_proto_to_py(v) for v in val]
    return val


def _build_prompt(q: RawQuestion, retrieved_context: Optional[str] = None) -> str:
    choices_text = "\n".join(
        f"  {OX_LETTERS[i-1]}({i}) {text}"
        for i, text in sorted(q.choices.items())
    )

    context_block = ""
    if retrieved_context:
        context_block = (
            "\n**CRITICAL: You MUST use the following retrieved legal text to ground your analysis. "
            "Do not use outside knowledge if this context is provided.**\n"
            f"[RAG Context - Real Legal Texts]\n{retrieved_context}\n[End of RAG Context]\n"
        )

    return (
        f"You are an expert legal analyst. Your task is to deconstruct a Korean Bar Exam "
        f"multiple-choice question into five separate True/False (O/X) statements and provide "
        f"a detailed, structured analysis for each.\n"
        f"{context_block}\n"
        f"**Source Question Analysis:**\n"
        f"*   **Question Stem:** {q.stem}\n"
        f"*   **Choices:**\n{choices_text}\n"
        f"*   **Correct Answer:** Choice {q.correct_choice}\n\n"
        f"**Your Task:**\n"
        f"1. Deconstruct each choice and rephrase it as a standalone O/X statement.\n"
        f"2. Verify if each statement is True (O) or False (X).\n"
        f"3. Justify each statement citing legal_basis, case_citation, explanation_core, keywords.\n"
        f"4. Grade importance (A=핵심, B=정기, C=드문).\n"
        f"5. Submit using the submit_ox_analysis tool."
    )


class MCQTransformer:
    def __init__(self, api_key: Optional[str] = None, concurrency: int = CONCURRENCY):
        project = os.getenv("VERTEXAI_PROJECT")
        location = os.getenv("VERTEXAI_LOCATION", "us-central1")
        if not project:
            raise RuntimeError("VERTEXAI_PROJECT not set")
        vertexai.init(project=project, location=location)
        self._model = GenerativeModel(MODEL, tools=[_OX_TOOL])
        self._http_client = httpx.AsyncClient(timeout=10.0)
        self._retriever = LegalRetriever(self._http_client)
        self._semaphore = asyncio.Semaphore(concurrency)

    async def transform_question(self, q: RawQuestion) -> Optional[TransformedQuestion]:
        async with self._semaphore:
            retrieved_context = ""
            case_numbers = re.findall(
                r"\d{4}[가-힣]+\d+", q.stem + " " + " ".join(q.choices.values())
            )
            if case_numbers:
                text = await self._retriever.fetch_precedent(case_numbers[0])
                if text:
                    retrieved_context += f"Case {case_numbers[0]}: {text}\n"

            prompt = _build_prompt(q, retrieved_context)

            for attempt in range(1, RETRY_LIMIT + 1):
                try:
                    response = await self._model.generate_content_async(
                        prompt,
                        generation_config=GenerationConfig(max_output_tokens=MAX_TOKENS),
                        tool_config=_TOOL_CONFIG,
                    )

                    raw = None
                    for part in response.candidates[0].content.parts:
                        if part.function_call:
                            raw = _proto_to_py(part.function_call.args)
                            break

                    if not raw:
                        log.error("%s: no function_call in Gemini response", q.raw_id)
                        return None

                    ox_list = []
                    for i, stmt in enumerate(raw.get("ox_statements", [])):
                        try:
                            ox_list.append(OXStatement(
                                letter=stmt.get("letter", OX_LETTERS[i]),
                                choice_number=stmt.get("choice_number", i + 1),
                                statement=stmt["statement"],
                                is_correct=bool(stmt["is_correct"]),
                                legal_basis=stmt.get("legal_basis"),
                                case_citation=stmt.get("case_citation"),
                                explanation_core=stmt.get("explanation_core"),
                                keywords=list(stmt.get("keywords", [])),
                                theory=stmt.get("theory"),
                                is_revised=bool(stmt.get("is_revised", False)),
                                revision_note=stmt.get("revision_note"),
                                importance=ImportanceGrade(stmt.get("importance", "B")),
                                explanation=stmt["explanation"],
                            ))
                        except (KeyError, ValidationError) as exc:
                            log.warning("%s stmt[%d] parse error: %s", q.raw_id, i, exc)

                    if not ox_list:
                        log.error("%s: zero valid OX statements", q.raw_id)
                        return None

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
                    log.error("Error transforming %s (attempt %d): %s", q.raw_id, attempt, e)
                    if attempt < RETRY_LIMIT:
                        await asyncio.sleep(RETRY_DELAY * (2 ** (attempt - 1)))
            return None

    async def transform_batch(
        self,
        questions: List[RawQuestion],
        checkpoint_path: Optional[Path] = None,
    ) -> List[TransformedQuestion]:
        results = []
        for q in questions:
            res = await self.transform_question(q)
            if res:
                results.append(res)
        return results
