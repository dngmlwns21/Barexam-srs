"""chat.py — AI Tutor endpoint using Vertex AI Gemini with RAG context."""
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
    """AI Tutor: explains a flashcard using Gemini with injected card context."""
    project = os.getenv("VERTEXAI_PROJECT")
    location = os.getenv("VERTEXAI_LOCATION", "us-central1")
    if not project:
        raise HTTPException(status_code=503, detail="VERTEXAI_PROJECT not configured")

    try:
        import vertexai
        from vertexai.generative_models import GenerativeModel, Content, Part
    except ImportError:
        raise HTTPException(status_code=503, detail="google-cloud-aiplatform package not installed")

    system_prompt = (
        "당신은 한국 변호사시험 전문 AI 튜터입니다. "
        "법령 근거와 판례를 인용하여 정확하고 간결하게 답변합니다. "
        "답변은 반드시 한국어로 작성하며, 관련 법조문 번호와 핵심 판례를 명시합니다. "
        "학습자가 이해하기 쉽도록 핵심 원리를 먼저 설명하고, 실제 시험 적용 방법을 안내합니다."
    )

    if request.context:
        system_prompt += f"\n\n[현재 학습 중인 카드 내용]\n{request.context}"

    try:
        vertexai.init(project=project, location=location)
        model = GenerativeModel("gemini-1.5-pro", system_instruction=system_prompt)

        history = []
        for h in (request.history or []):
            role = "user" if h.role == "user" else "model"
            history.append(Content(role=role, parts=[Part.from_text(h.content)]))

        chat = model.start_chat(history=history)
        response = await chat.send_message_async(request.message)
        answer = response.text or "응답을 생성할 수 없습니다."
        return ChatResponse(response=answer)
    except Exception as e:
        log.error("Gemini API error: %s", e)
        raise HTTPException(status_code=502, detail=f"AI 튜터 오류: {e}")
