import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Choice, Question

router = APIRouter()


class ExplainRequest(BaseModel):
    question_id: uuid.UUID
    choice_id: uuid.UUID
    user_query: str


class ExplainResponse(BaseModel):
    explanation: str


@router.post(
    "/explain",
    summary="Get AI explanation for a card",
    response_model=ExplainResponse,
)
async def explain_with_ai(request: ExplainRequest, db: Session = Depends(get_db)):
    """
    AI 튜터에게 특정 카드에 대한 추가 설명을 요청합니다.
    (현재는 플레이스홀더 응답을 반환합니다.)
    """
    question = db.get(Question, request.question_id)
    choice = db.get(Choice, request.choice_id)

    if not question or not choice:
        raise HTTPException(status_code=404, detail="Card not found")

    # TODO: LLM (Claude/GPT) 호출 로직 구현
    # 1. question, choice 정보와 request.user_query를 조합하여 프롬프트 생성
    # 2. LLM API 호출
    # 3. LLM 응답 반환

    llm_response = (
        f"'{request.user_query}'에 대한 AI 해설입니다. "
        f"문제: '{question.stem[:30]}...', "
        f"선택지: '{choice.content[:30]}...' (AI 기능 개발 중)"
    )

    return {"explanation": llm_response}
