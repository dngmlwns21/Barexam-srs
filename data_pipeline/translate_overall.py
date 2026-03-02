"""
translate_overall.py — Translate English/mixed explanations to Korean using Vertex AI Gemini.

Handles:
  - questions.overall_explanation  (pure English + Korean/English mixed)
  - choices.explanation            (pure English + Korean/English mixed)
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path
from typing import Dict, List, Tuple

import asyncpg
import vertexai
from dotenv import load_dotenv
from vertexai.generative_models import GenerationConfig, GenerativeModel

load_dotenv(Path(__file__).resolve().parents[1] / "backend" / ".env")
log = logging.getLogger(__name__)

BATCH_SIZE = 20
CONCURRENCY = 10
MODEL = "gemini-2.0-flash"

# 영어 5자 이상 연속 → 번역 대상 (순수 영어 + 혼합 모두 포함)
_ENG_RE = re.compile(r'[a-zA-Z]{5,}')


def has_english_content(text: str) -> bool:
    """순수 영어 또는 영어가 섞인 텍스트를 모두 감지."""
    return bool(text and _ENG_RE.search(text))


def _translate_batch_sync(
    model: GenerativeModel,
    items: List[Tuple[str, str]],
    mode: str = "overall",
) -> Dict[str, str]:
    """items: [(id, text), ...], mode: 'overall' | 'choice'"""
    numbered = "\n\n".join(f"[{i+1}] {text}" for i, (_, text) in enumerate(items))

    if mode == "overall":
        instruction = (
            "다음은 한국 변호사시험 문제들의 총평(overall explanation)입니다. "
            "영어 문장 또는 깨진 한국어가 포함된 경우 모두 자연스러운 한국어 법학 문체로 "
            "완전히 새로 작성하세요. "
        )
    else:
        instruction = (
            "다음은 한국 변호사시험 O/X 지문의 해설(explanation)입니다. "
            "영어 문장 또는 깨진 한국어가 포함된 경우 모두 자연스러운 한국어 법학 문체로 "
            "완전히 새로 작성하세요. "
        )

    prompt = (
        instruction
        + "번호([1], [2], ...)를 그대로 유지하고 번역·재작성된 한국어 텍스트만 출력하세요.\n\n"
        + numbered
    )
    response = model.generate_content(
        prompt,
        generation_config=GenerationConfig(max_output_tokens=4096),
    )
    text = response.text
    results: Dict[str, str] = {}
    parts = re.split(r'\[(\d+)\]', text)
    for i in range(1, len(parts), 2):
        idx = int(parts[i]) - 1
        content = parts[i + 1].strip() if i + 1 < len(parts) else ""
        if 0 <= idx < len(items):
            results[items[idx][0]] = content
    return results


async def _process_items(
    conn: asyncpg.Connection,
    model: GenerativeModel,
    to_translate: List[Tuple[str, str]],
    update_sql: str,
    mode: str,
    label: str,
) -> int:
    semaphore = asyncio.Semaphore(CONCURRENCY)
    db_lock = asyncio.Lock()
    done = 0
    loop = asyncio.get_event_loop()

    async def process_batch(batch_idx: int, batch: List[Tuple[str, str]]):
        nonlocal done
        async with semaphore:
            try:
                translations = await loop.run_in_executor(
                    None, _translate_batch_sync, model, batch, mode
                )
                if translations:
                    async with db_lock:
                        await conn.executemany(
                            update_sql,
                            [(qid, txt) for qid, txt in translations.items()]
                        )
                    done += len(translations)
                    log.info("  [%s] %d/%d 완료 (배치 %d)", label, done, len(to_translate), batch_idx)
            except Exception as e:
                log.error("[%s] 배치 %d 오류: %s", label, batch_idx, e)

    tasks = [
        process_batch(i // BATCH_SIZE + 1, to_translate[i:i + BATCH_SIZE])
        for i in range(0, len(to_translate), BATCH_SIZE)
    ]
    await asyncio.gather(*tasks)
    return done


async def run():
    db_url = os.getenv("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
    project = os.getenv("VERTEXAI_PROJECT")
    location = os.getenv("VERTEXAI_LOCATION", "us-central1")

    if not project:
        log.error("VERTEXAI_PROJECT 환경변수가 설정되지 않았습니다.")
        return

    vertexai.init(project=project, location=location)
    model = GenerativeModel(MODEL)

    conn = await asyncpg.connect(db_url)
    try:
        # ── 1. questions.overall_explanation ────────────────────────────────
        q_rows = await conn.fetch(
            "SELECT id, overall_explanation FROM questions WHERE overall_explanation IS NOT NULL"
        )
        overall_items = [
            (str(r["id"]), r["overall_explanation"])
            for r in q_rows if has_english_content(r["overall_explanation"])
        ]
        log.info("overall_explanation 번역 대상: %d건", len(overall_items))

        done_overall = await _process_items(
            conn, model, overall_items,
            "UPDATE questions SET overall_explanation=$2 WHERE id=$1",
            mode="overall",
            label="overall_explanation",
        )
        log.info("overall_explanation 번역 완료: %d건", done_overall)

        # ── 2. choices.explanation ───────────────────────────────────────────
        c_rows = await conn.fetch(
            "SELECT id, explanation FROM choices "
            "WHERE choice_number >= 101 AND explanation IS NOT NULL"
        )
        choice_items = [
            (str(r["id"]), r["explanation"])
            for r in c_rows if has_english_content(r["explanation"])
        ]
        log.info("choices.explanation 번역 대상: %d건", len(choice_items))

        done_choices = await _process_items(
            conn, model, choice_items,
            "UPDATE choices SET explanation=$2 WHERE id=$1",
            mode="choice",
            label="choices.explanation",
        )
        log.info("choices.explanation 번역 완료: %d건", done_choices)

    finally:
        await conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run())
