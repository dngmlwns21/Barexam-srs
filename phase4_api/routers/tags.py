from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_current_user, get_db
from ..models import QuestionTag, Tag
from ..schemas import TagOut

router = APIRouter()


@router.get("/", response_model=List[TagOut])
async def list_tags(
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    # Tags with usage count
    result = await db.execute(
        select(Tag, func.count(QuestionTag.question_id).label("usage"))
        .outerjoin(QuestionTag, QuestionTag.tag_id == Tag.id)
        .group_by(Tag.id)
        .order_by(func.count(QuestionTag.question_id).desc())
    )
    rows = result.all()
    return [
        TagOut(id=tag.id, name=tag.name, color_hex=tag.color_hex, usage=usage)
        for tag, usage in rows
    ]
