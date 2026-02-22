from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Float, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_current_user, get_db
from ..models import Flashcard, Question, ReviewLog, User, UserProgress
from ..schemas import DueCardOut
from ..utils import build_due_card_out, up_load_opts

router = APIRouter()


@router.get("/quick-scan", response_model=List[DueCardOut])
async def quick_scan(
    mode: str = Query(
        ...,
        description="리뷰 전략: 'failure' (최근 틀린 문제), "
        "'newest' (최신순), 'favorites' (즐겨찾기)",
        pattern="^(failure|newest|favorites)$",
    ),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    SRS 채점 없이 빠른 시험 전 복습을 위한 카드 목록을 가져옵니다.

    모드:
    - failure:   사용자가 가장 최근에 틀린 카드
    - newest:    가장 최근에 추가된 질문
    - favorites: 사용자가 즐겨찾기한 카드
    """
    opts = up_load_opts()

    base_stmt = (
        select(UserProgress)
        .join(Flashcard, Flashcard.id == UserProgress.flashcard_id)
        .join(Question, Question.id == Flashcard.question_id)
        .options(*opts)
        .where(UserProgress.user_id == current_user.id)
    )

    if mode == "failure":
        # 사용자의 각 플래시카드에 대한 최신 리뷰 로그를 찾는 CTE
        latest_review_cte = (
            select(
                ReviewLog.flashcard_id,
                ReviewLog.is_correct,
                func.row_number()
                .over(
                    partition_by=ReviewLog.flashcard_id,
                    order_by=ReviewLog.created_at.desc(),
                )
                .label("rn"),
            )
            .where(ReviewLog.user_id == current_user.id)
            .cte("latest_review")
        )

        # 최신 리뷰가 오답인 플래시카드 ID를 가져오는 서브쿼리
        incorrect_flashcard_ids_subquery = select(
            latest_review_cte.c.flashcard_id
        ).where(
            (latest_review_cte.c.rn == 1) & (latest_review_cte.c.is_correct == False)
        )

        stmt = base_stmt.where(
            UserProgress.flashcard_id.in_(incorrect_flashcard_ids_subquery)
        )

    elif mode == "newest":
        stmt = base_stmt.order_by(Question.created_at.desc())

    elif mode == "favorites":
        stmt = base_stmt.where(UserProgress.is_starred == True).order_by(
            UserProgress.updated_at.desc()
        )

    stmt = stmt.limit(limit)
    result = await db.execute(stmt)
    return [build_due_card_out(up) for up in result.scalars().all()]
