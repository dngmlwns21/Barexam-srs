"""chat.py — AI Tutor endpoint using Claude API with RAG context."""
from __future__ import annotations

import os
import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

log = logging.getLogger(__name__)

router = APIRouter()


class ChatMessage(BaseModel):
    role: str   # 'user' | 'assistant'
    content: str


class ChatRequest(BaseModel):
    card_id: str
    message: str
    context: Optional[str] = None   # card context (stem + choice statement)
    history: Optional[List[ChatMessage]] = None


class ChatResponse(BaseModel):
    response: str


@router.post("/explain", response_model=ChatResponse)
async def chat_explain(request: ChatRequest):
    """AI Tutor: explains a flashcard using Claude with injected card context."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY not configured")

    try:
        import anthropic
    except ImportError:
        raise HTTPException(status_code=503, detail="anthropic package not installed")

    system_prompt = (
        "당신은 한국 변호사시험 전문 AI 튜터입니다. "
        "법령 근거와 판례를 인용하여 정확하고 간결하게 답변합니다. "
        "답변은 반드시 한국어로 작성하며, 관련 법조문 번호와 핵심 판례를 명시합니다. "
        "학습자가 이해하기 쉽도록 핵심 원리를 먼저 설명하고, 실제 시험 적용 방법을 안내합니다."
    )

    if request.context:
        system_prompt += f"\n\n[현재 학습 중인 카드 내용]\n{request.context}"

    messages = []
    for h in (request.history or []):
        messages.append({"role": h.role, "content": h.content})
    messages.append({"role": "user", "content": request.message})

    try:
        client = anthropic.AsyncAnthropic(api_key=api_key)
        resp = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=system_prompt,
            messages=messages,
        )
        answer = resp.content[0].text if resp.content else "응답을 생성할 수 없습니다."
        return ChatResponse(response=answer)
    except Exception as e:
        log.error("Claude API error: %s", e)
        raise HTTPException(status_code=502, detail=f"AI 튜터 오류: {e}")
