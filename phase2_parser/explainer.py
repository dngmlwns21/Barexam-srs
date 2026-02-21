"""
AI explanation generator using Anthropic Batches API.
Generates Korean legal explanations (해설) for each question.

Usage:
  submit_batch(questions)     → batch_id
  poll_batch(batch_id)        → waits until done
  collect_results(batch_id)   → adds explanations to questions in-place
"""

import os
import time
import logging
from typing import List

import anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

log = logging.getLogger(__name__)

MODEL = "claude-opus-4-6"
MAX_TOKENS = 2048

SYSTEM_PROMPT = """당신은 한국 법조계 전문가이자 변호사시험 교육 전문가입니다.
주어진 변호사시험 선택형 문제에 대해 상세한 해설을 한국어로 작성하세요.

해설 형식:
1. 정답: [번호]
2. 핵심 법리: 관련 법조문과 핵심 법리를 간결하게 설명
3. 선택지 분석: 각 선택지가 옳은지 틀린지 이유와 함께 분석
4. 관련 판례/법령: 핵심 판례나 법조문 인용 (있는 경우)

해설은 수험생이 이해하기 쉽도록 명확하고 체계적으로 작성하세요."""


def _format_question(q: dict) -> str:
    """Format a question dict as a prompt string."""
    lines = []
    subject = q.get("subject", "")
    session = q.get("exam_session", "")
    qnum = q.get("question_number", "")
    lines.append(f"[제{session}회 변호사시험 {subject} 선택형 문제 {qnum}번]")
    lines.append("")
    lines.append(q.get("question_text", ""))
    lines.append("")

    choices = q.get("choices", {})
    for k in sorted(choices.keys(), key=lambda x: int(x)):
        circle = ["①", "②", "③", "④", "⑤"][int(k) - 1]
        lines.append(f"{circle} {choices[k]}")

    answer = q.get("answer")
    if answer:
        lines.append(f"\n정답: {answer}번")

    return "\n".join(lines)


def submit_batch(questions: List[dict], client: anthropic.Anthropic) -> str:
    """Submit all questions to Batches API. Returns batch_id."""
    requests = []
    for q in questions:
        if not q.get("question_text") or not q.get("choices"):
            continue
        requests.append(
            Request(
                custom_id=q["id"],
                params=MessageCreateParamsNonStreaming(
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    thinking={"type": "adaptive"},
                    system=SYSTEM_PROMPT,
                    messages=[
                        {"role": "user", "content": _format_question(q)}
                    ],
                ),
            )
        )

    if not requests:
        raise ValueError("No valid questions to submit.")

    log.info("Submitting %d questions to Batches API…", len(requests))
    batch = client.messages.batches.create(requests=requests)
    log.info("Batch ID: %s", batch.id)
    return batch.id


def poll_batch(batch_id: str, client: anthropic.Anthropic, poll_interval: int = 60) -> None:
    """Block until batch processing ends."""
    log.info("Polling batch %s…", batch_id)
    while True:
        batch = client.messages.batches.retrieve(batch_id)
        counts = batch.request_counts
        log.info(
            "Status: %s | processing=%d succeeded=%d errored=%d",
            batch.processing_status,
            counts.processing,
            counts.succeeded,
            counts.errored,
        )
        if batch.processing_status == "ended":
            break
        time.sleep(poll_interval)


def collect_results(batch_id: str, questions: List[dict], client: anthropic.Anthropic) -> int:
    """
    Fetch batch results and attach explanation text to each question in-place.
    Returns number of explanations successfully added.
    """
    q_index = {q["id"]: q for q in questions}
    count = 0

    for result in client.messages.batches.results(batch_id):
        if result.result.type == "succeeded":
            q = q_index.get(result.custom_id)
            if q is None:
                continue
            # Extract only text blocks (skip thinking blocks)
            text_parts = [
                block.text
                for block in result.result.message.content
                if block.type == "text"
            ]
            q["explanation"] = "\n\n".join(text_parts).strip()
            count += 1
        elif result.result.type == "errored":
            log.warning("Error for %s: %s", result.custom_id, result.result.error)

    log.info("Attached %d explanations.", count)
    return count


def explain_single(q: dict, client: anthropic.Anthropic) -> str:
    """Generate explanation for a single question (streaming). For testing."""
    with client.messages.stream(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        thinking={"type": "adaptive"},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _format_question(q)}],
    ) as stream:
        return stream.get_final_message().content[-1].text
