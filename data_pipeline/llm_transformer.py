"""
llm_transformer.py — Transform MCQ questions into O/X flashcards via Claude API.

Uses Claude tool_use (structured output) to generate instructor-level O/X
statements for each of the 5 choices in a Korean bar exam question.

Usage:
    python -m data_pipeline.llm_transformer \
        --input data/bar_raw.json \
        --output data/transformed.json \
        --concurrency 5
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

load_dotenv(Path(__file__).resolve().parents[1] / "backend" / ".env")

log = logging.getLogger(__name__)

MODEL        = "claude-3-sonnet-20240229"
MAX_TOKENS   = 4096
CONCURRENCY  = 3       # parallel LLM calls
RETRY_LIMIT  = 5
RETRY_DELAY  = 2.0     # base seconds for exponential backoff


# ── Tool schema for structured output ────────────────────────────────────────

OX_TOOL = {
    "name": "submit_ox_analysis",
    "description": (
        "Submit a structured O/X analysis for a Korean bar exam MCQ question. "
        "Each of the 5 answer choices is analyzed as an independent legal proposition."
    ),
    "input_schema": {
        "type": "object",
        "required": ["overall_explanation", "ox_statements"],
        "properties": {
            "overall_explanation": {
                "type": "string",
                "description": (
                    "Overall instructor-level explanation covering the core legal "
                    "issue of the question in 2-4 sentences."
                ),
            },
            "ox_statements": {
                "type": "array",
                "minItems": 1,
                "maxItems": 5,
                "items": {
                    "type": "object",
                    "required": [
                        "letter", "choice_number", "statement",
                        "is_correct", "importance",
                        "conclusion", "core_reasoning", "detailed_explanation", "citation",
                        "explanation_core", "explanation", "keywords",
                    ],
                    "properties": {
                        "letter": {
                            "type": "string",
                            "enum": OX_LETTERS,
                            "description": "Hangul letter (가/나/다/라/마) for this OX card.",
                        },
                        "choice_number": {
                            "type": "integer",
                            "minimum": 1, "maximum": 5,
                            "description": "Original MCQ choice number (1–5).",
                        },
                        "statement": {
                            "type": "string",
                            "description": (
                                "Rewritten as a standalone, self-contained legal proposition "
                                "a student can judge O or X without seeing the original question."
                            ),
                        },
                        "is_correct": {
                            "type": "boolean",
                            "description": (
                                "True (O) if the statement is legally correct, "
                                "False (X) if legally incorrect."
                            ),
                        },
                        "legal_basis": {
                            "type": "string",
                            "description": (
                                "Exact statutory basis, e.g., '민법 제390조 제2항'. "
                                "Omit if not applicable."
                            ),
                        },
                        "case_citation": {
                            "type": "string",
                            "description": (
                                "Exact Supreme Court / Constitutional Court citation, "
                                "e.g., '대법원 2022.12.29. 선고 2022다12345 판결'. "
                                "Omit if not applicable."
                            ),
                        },
                        "conclusion": {
                            "type": "string",
                            "enum": ["O", "X", "O, X"],
                            "description": (
                                "판단 결론: 'O' (법적으로 옳음), 'X' (법적으로 틀림), "
                                "또는 'O, X' (조건에 따라 다름). "
                                "교재의 정답 뱃지(□)에 표시될 값입니다."
                            ),
                        },
                        "core_reasoning": {
                            "type": "string",
                            "description": (
                                "결론 도출의 핵심 법원리를 한 문장으로 직접 서술. "
                                "예: '채무불이행으로 인한 손해배상은 이행이익을 원칙으로 한다.' "
                                "교재의 굵은 핵심 문장에 해당합니다."
                            ),
                        },
                        "detailed_explanation": {
                            "type": "string",
                            "description": (
                                "단계별 논리 전개. 판례가 복잡하거나 논거가 여럿이면 "
                                "①, ②, ③으로 각 논거를 명확히 분리하세요. "
                                "핵심 법률용어(예: **손해배상**, **이행이익**)는 **굵게** 강조하세요. "
                                "예: '① **채무불이행**이 성립하면 ② 채권자는 **이행이익** 상당액을 청구할 수 있으나, "
                                "③ 채권자가 계약을 해제한 경우에는 **신뢰이익**도 청구 가능하다.'"
                            ),
                        },
                        "citation": {
                            "type": "string",
                            "description": (
                                "정확한 법적 근거를 괄호 안에 표기. "
                                "법령 예: '(민법 제390조 제2항)' "
                                "판례 예: '(대법원 2018. 3. 25. 선고 2017다1234 판결)' "
                                "둘 다 있으면 세미콜론으로 구분: '(민법 제390조; 대법원 2018다1234)'"
                            ),
                        },
                        "explanation_core": {
                            "type": "string",
                            "description": (
                                "core_reasoning과 동일 내용. "
                                "A single, core sentence that directly explains the reason for O/X."
                            ),
                        },
                        "keywords": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "A list of 3-5 essential legal keywords for this statement, "
                                "e.g., ['손해배상', '이행이익', '신뢰이익']."
                            ),
                        },
                        "theory": {
                            "type": "string",
                            "description": (
                                "Dominant academic theory if relevant, e.g. '다수설'. "
                                "Omit if not applicable."
                            ),
                        },
                        "is_revised": {
                            "type": "boolean",
                            "description": (
                                "True if the answer may differ due to a recent law revision "
                                "or new Supreme Court ruling (after exam year)."
                            ),
                        },
                        "revision_note": {
                            "type": "string",
                            "description": "Explain the revision if is_revised=true.",
                        },
                        "importance": {
                            "type": "string",
                            "enum": ["A", "B", "C"],
                            "description": (
                                "A=핵심 반복 출제 (must-know), "
                                "B=표준 정기 출제, "
                                "C=주변 가끔 출제."
                            ),
                        },
                        "explanation": {
                            "type": "string",
                            "description": (
                                "detailed_explanation과 동일 내용. "
                                "Full step-by-step explanation using ①②③ for multiple arguments "
                                "and **bold** for key legal terms."
                            ),
                        },
                    },
                },
            },
        },
    },
}


# ── Prompt builder ────────────────────────────────────────────────────────────

def _build_prompt(q: RawQuestion, retrieved_context: Optional[str] = None) -> str:
    source_tag = "[변시]" if q.source == Source.BAR_EXAM else "[법전협]"
    session_info = (
        f"제{q.exam_session}회 변호사시험" if q.exam_session
        else f"{q.year}년 {q.month or ''}월 모의시험" if q.year
        else "시험"
    )
    choices_text = "\n".join(
        f"  {OX_LETTERS[i-1]}({i}) {text}"
        for i, text in sorted(q.choices.items())
    )
    correct_letter = OX_LETTERS[q.correct_choice - 1]

    context_section = ""
    if retrieved_context:
        context_section = f"""
[참고 자료]
아래는 본 문제와 관련된 법령 및 판례입니다. 이 내용을 바탕으로 정확한 해설을 생성하세요.
---
{retrieved_context}
---
"""

    return f"""당신은 한국 변호사시험 전문 강사입니다. 아래 5지선다형 문제를 분석하여 각 선택지를 독립적인 O/X 명제로 변환하세요.

[출처] {source_tag} {session_info} | {q.subject} | 문 {q.question_number}번

[문제]
{q.stem}

[선택지]
{choices_text}

[정답] {correct_letter}번 ({q.correct_choice}번) → 이 선택지의 법률적 명제는 O (정답)
{context_section}
[변환 지침] — UNION 변호사시험 OX 교재 형식
1. 각 선택지를 문제 지문에 의존하지 않는 독립적 O/X 명제(statement)로 재작성하세요.
   - 예: "甲은 ~ 할 수 있다" → "채무자가 [구체적 상황]인 경우 채권자는 ~ 할 수 있다."
2. conclusion: is_correct와 동일하게 "O" 또는 "X". 조건부 정답이면 "O, X".
   - 교재의 □ 뱃지에 표시됩니다.
3. core_reasoning: 결론의 핵심 법원리를 한 문장으로 직접 서술하세요.
   - 예: "계약 해제 후에도 채권자는 이행이익과 신뢰이익 중 선택하여 청구할 수 있다."
4. detailed_explanation: 단계별 논리 전개 (3~6문장 또는 ①②③ 번호 형식).
   - 판례의 논거가 여럿이면 반드시 ①, ②, ③으로 분리하세요.
   - **핵심 법률용어**는 **굵게** 강조하세요. 예: **손해배상**, **이행이익**
   - 마지막에 "따라서 이 명제는 O/X이다." 형식으로 결론을 맺으세요.
5. citation: 정확한 법적 근거를 괄호 안에 표기하세요.
   - 법령: "(민법 제390조 제2항)"
   - 판례: "(대법원 2018. 3. 25. 선고 2017다1234 판결)"
   - 둘 다: "(민법 제390조; 대법원 2018다1234 판결)"
6. legal_basis: 조문 번호만, explanation_core: core_reasoning과 동일내용, explanation: detailed_explanation과 동일내용
7. 키워드(keywords): 3~5개 핵심 법률 용어 리스트.
8. 중요도: 반복 출제 핵심 쟁점=A, 정기 출제=B, 드문 쟁점=C
9. 최근 법령 개정 또는 판례 변경으로 정답이 바뀔 수 있으면 is_revised=true로 설정하세요.
10. 모든 내용은 한국어로, 강사 수준의 정확성으로 작성하세요.

submit_ox_analysis 도구를 사용해 결과를 제출하세요."""


# ── LLM caller ────────────────────────────────────────────────────────────────

class MCQTransformer:
    """Async transformer: RawQuestion → TransformedQuestion via Claude."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        concurrency: int = CONCURRENCY,
    ) -> None:
        key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        self._client = anthropic.AsyncClient(api_key=key)
        self._http_client = httpx.AsyncClient(timeout=10.0)
        self._retriever = LegalRetriever(self._http_client)
        self._semaphore = asyncio.Semaphore(concurrency)

    async def transform_question(self, q: RawQuestion) -> Optional[TransformedQuestion]:
        """Transform a single RawQuestion. Returns None on unrecoverable failure."""
        async with self._semaphore:
            for attempt in range(1, RETRY_LIMIT + 1):
                try:
                    result = await self._call_api(q) # Direct await
                    return result
                except anthropic.RateLimitError:
                    if attempt == RETRY_LIMIT:
                        log.error("%s rate-limited: max retries exceeded", q.raw_id)
                        return None
                    wait = RETRY_DELAY * (2 ** (attempt - 1))  # 2s, 4s, 8s, 16s, 32s
                    log.warning("%s rate-limited, retry %d/%d in %.1fs", q.raw_id, attempt, RETRY_LIMIT, wait)
                    await asyncio.sleep(wait)
                except anthropic.APIError as exc:
                    log.error("%s API error (attempt %d): %s", q.raw_id, attempt, exc)
                    if attempt == RETRY_LIMIT:
                        return None
                    await asyncio.sleep(RETRY_DELAY * (2 ** (attempt - 1)))
                except Exception as exc:
                    log.error("%s unexpected error: %s", q.raw_id, exc)
                    return None
        return None

    async def _call_api(self, q: RawQuestion) -> TransformedQuestion:
        """Asynchronous API call with RAG context."""

        # RAG: Fetch context (simple placeholder logic)
        # TODO: Implement more sophisticated logic to parse statute/precedent from question text
        retrieved_context = ""
        # A simple regex to find case numbers
        case_numbers = re.findall(r"\b\d{4}다\d+\b", q.stem + " " + " ".join(q.choices.values()))
        if case_numbers:
            # Fetch the first found precedent for simplicity
            precedent_text = await self._retriever.fetch_precedent(case_numbers[0])
            if precedent_text:
                retrieved_context += f"관련 판례 ({case_numbers[0]}):\n{precedent_text}\n\n"

        prompt = _build_prompt(q, retrieved_context or None)

        response = await self._client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            tools=[OX_TOOL],
            tool_choice={"type": "tool", "name": "submit_ox_analysis"},
            messages=[{"role": "user", "content": prompt}],
        )

        # Extract tool_use block
        tool_block = next(
            (b for b in response.content if b.type == "tool_use"), None
        )
        if not tool_block:
            raise ValueError(f"No tool_use in response for {q.raw_id}")

        raw: Dict[str, Any] = tool_block.input

        # Parse OX statements
        ox_list: List[OXStatement] = []
        for i, stmt in enumerate(raw.get("ox_statements", [])):
            try:
                is_correct = bool(stmt["is_correct"])
                # Derive conclusion from LLM output or is_correct
                conclusion = stmt.get("conclusion", "O" if is_correct else "X")
                # core_reasoning: prefer new field, fall back to explanation_core
                core_reasoning = stmt.get("core_reasoning") or stmt.get("explanation_core")
                # detailed_explanation: prefer new field, fall back to explanation
                detailed_explanation = stmt.get("detailed_explanation") or stmt.get("explanation", "")
                # citation: prefer new field, construct from legal_basis/case_citation
                citation = stmt.get("citation")
                if not citation:
                    parts = [p for p in [stmt.get("legal_basis"), stmt.get("case_citation")] if p]
                    if parts:
                        citation = "(" + "; ".join(parts) + ")"
                ox_list.append(
                    OXStatement(
                        letter=stmt.get("letter", OX_LETTERS[i]),
                        choice_number=stmt.get("choice_number", i + 1),
                        statement=stmt["statement"],
                        is_correct=is_correct,
                        # Textbook-style fields
                        conclusion=conclusion,
                        core_reasoning=core_reasoning,
                        detailed_explanation=detailed_explanation,
                        citation=citation,
                        # Legacy / enrichment fields
                        legal_basis=stmt.get("legal_basis"),
                        case_citation=stmt.get("case_citation"),
                        explanation_core=core_reasoning,   # mirror to DB column
                        keywords=stmt.get("keywords", []),
                        theory=stmt.get("theory"),
                        is_revised=bool(stmt.get("is_revised", False)),
                        revision_note=stmt.get("revision_note"),
                        importance=ImportanceGrade(stmt.get("importance", "B")),
                        explanation=detailed_explanation,  # mirror to DB column
                    )
                )
            except (KeyError, ValidationError) as exc:
                log.warning("%s stmt[%d] parse error: %s", q.raw_id, i, exc)

        if not ox_list:
            raise ValueError(f"Zero valid OX statements for {q.raw_id}")

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

    async def transform_batch(
        self,
        questions: List[RawQuestion],
        checkpoint_path: Optional[Path] = None,
    ) -> List[TransformedQuestion]:
        """
        Transform all questions with progress logging.
        Saves checkpoint JSON after each successful transform.
        """
        # Load existing checkpoint
        done_ids: set = set()
        results: List[TransformedQuestion] = []
        if checkpoint_path and checkpoint_path.exists():
            prev = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            results = [TransformedQuestion.model_validate(r) for r in prev]
            done_ids = {r.raw_id for r in results}
            log.info("Resuming: %d already done", len(done_ids))

        pending = [q for q in questions if q.raw_id not in done_ids]
        log.info("Transforming %d questions (%d pending)", len(questions), len(pending))

        sem = asyncio.Semaphore(1)  # checkpoint writes are serialized

        async def transform_and_save(q: RawQuestion) -> None:
            tq = await self.transform_question(q)
            if tq:
                results.append(tq)
                log.info(
                    "  ✓ %s → %d OX statements", q.raw_id, len(tq.ox_statements)
                )
                if checkpoint_path:
                    async with sem:
                        checkpoint_path.write_text(
                            json.dumps(
                                [r.model_dump(mode="json") for r in results],
                                ensure_ascii=False, indent=2,
                            ),
                            encoding="utf-8",
                        )
            else:
                log.warning("  ✗ FAILED: %s", q.raw_id)

        await asyncio.gather(*[transform_and_save(q) for q in pending])

        log.info("Done: %d/%d transformed", len(results), len(questions))
        return results


# ── CLI ───────────────────────────────────────────────────────────────────────

async def _main(args: argparse.Namespace) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    input_path = Path(args.input)
    if not input_path.exists():
        sys.exit(f"Input file not found: {input_path}")

    raw = json.loads(input_path.read_text(encoding="utf-8"))
    items = raw if isinstance(raw, list) else raw.get("questions", [])
    questions = [RawQuestion.model_validate(q) for q in items]

    if args.limit:
        questions = questions[: args.limit]

    transformer = MCQTransformer(concurrency=args.concurrency)
    checkpoint  = Path(args.output + ".checkpoint.json")

    results = await transformer.transform_batch(
        questions,
        checkpoint_path=checkpoint,
    )

    out_path = Path(args.output)
    out_path.write_text(
        json.dumps(
            [r.model_dump(mode="json") for r in results],
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Saved {len(results)} transformed questions → {out_path}")
    if checkpoint.exists():
        checkpoint.unlink()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Transform MCQ → OX via Claude")
    parser.add_argument("--input",       required=True, help="Input raw JSON")
    parser.add_argument("--output",      required=True, help="Output transformed JSON")
    parser.add_argument("--concurrency", type=int, default=CONCURRENCY)
    parser.add_argument("--limit",       type=int, default=None)
    asyncio.run(_main(parser.parse_args()))
