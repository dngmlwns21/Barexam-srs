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
CONCURRENCY = 20     # paid tier: 1000 QPM → 20 concurrent is safe
RETRY_LIMIT = 5
RETRY_DELAY = 10.0   # base wait on 429 (doubles each retry: 10s, 20s, 40s…)

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
            "\n**중요: 아래 검색된 법령 원문을 반드시 분석의 근거로 사용하십시오. "
            "이 문맥이 제공된 경우 외부 지식보다 이 내용을 우선합니다.**\n"
            f"[RAG 문맥 - 실제 법령 원문]\n{retrieved_context}\n[RAG 문맥 끝]\n"
        )

    return (
        f"당신은 한국 변호사시험 전문 법률 분석가입니다. "
        f"모든 분석과 설명은 반드시 한국어로 작성하십시오.\n"
        f"주어진 5지선다형 문제를 5개의 독립적인 O/X 지문으로 분해하고 "
        f"각 지문에 대해 상세한 구조적 분석을 제공하십시오.\n"
        f"{context_block}\n"
        f"**문제 분석:**\n"
        f"*   **문제 지문:** {q.stem}\n"
        f"*   **선택지:**\n{choices_text}\n"
        f"*   **정답:** {q.correct_choice}번\n\n"
        f"**수행 과제:**\n"
        f"1. 각 선택지를 독립적인 O/X 지문으로 재구성하십시오.\n"
        f"2. 각 지문의 옳고 그름(O/X)을 판단하십시오.\n"
        f"3. 관련 법령(legal_basis), 판례(case_citation), 핵심 해설(explanation_core), 키워드(keywords)를 근거로 설명하십시오.\n"
        f"4. 중요도를 등급화하십시오 (A=핵심·반복출제, B=표준·정기출제, C=드문·가끔출제).\n"
        f"5. overall_explanation에는 문제 전체에 대한 총평을 한국어로 작성하십시오.\n"
        f"6. submit_ox_analysis 도구를 사용하여 결과를 제출하십시오.\n"
        f"\n**중요: statement, explanation, explanation_core, overall_explanation 등 모든 텍스트 필드는 반드시 한국어로 작성하십시오.**"
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

                    log.info("  ✓ %s → %d statements", q.raw_id, len(ox_list))
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
        tasks = [self.transform_question(q) for q in questions]
        results_raw = await asyncio.gather(*tasks, return_exceptions=True)
        results = []
        for r in results_raw:
            if isinstance(r, TransformedQuestion):
                results.append(r)
            elif isinstance(r, Exception):
                log.error("Batch task exception: %s", r)
        return results
