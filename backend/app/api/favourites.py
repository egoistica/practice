from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_db
from app.models.favourite import Favourite
from app.models.lecture import Lecture
from app.models.user import User
from app.schemas.engagement import (
    FavouriteLectureResponse,
    FavouritesListResponse,
)

router = APIRouter(prefix="/favourites", tags=["favourites"])


def _to_favourite_response(favourite: Favourite, lecture: Lecture) -> FavouriteLectureResponse:
    return FavouriteLectureResponse(
        lecture_id=lecture.id,
        title=lecture.title,
        status=lecture.status.value if hasattr(lecture.status, "value") else str(lecture.status),
        processing_progress=lecture.processing_progress,
        created_at=lecture.created_at,
        favourited_at=favourite.created_at,
    )


@router.get("", response_model=FavouritesListResponse)
async def list_favourites(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FavouritesListResponse:
    rows = (
        await db.execute(
            select(Favourite, Lecture)
            .join(Lecture, Favourite.lecture_id == Lecture.id)
            .where(Favourite.user_id == user.id, Lecture.user_id == user.id)
            .order_by(Favourite.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
    ).all()
    total = int(
        (
            await db.execute(
                select(func.count())
                .select_from(Favourite)
                .where(Favourite.user_id == user.id)
            )
        ).scalar_one()
    )
    return FavouritesListResponse(
        items=[_to_favourite_response(favourite, lecture) for favourite, lecture in rows],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.post("/{lecture_id}", response_model=FavouriteLectureResponse, status_code=status.HTTP_201_CREATED)
async def add_to_favourites(
    lecture_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FavouriteLectureResponse:
    lecture = await db.get(Lecture, lecture_id)
    if lecture is None or lecture.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lecture not found")

    existing = (
        await db.execute(
            select(Favourite).where(
                Favourite.user_id == user.id,
                Favourite.lecture_id == lecture_id,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        existing = Favourite(user_id=user.id, lecture_id=lecture_id)
        db.add(existing)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            existing = (
                await db.execute(
                    select(Favourite).where(
                        Favourite.user_id == user.id,
                        Favourite.lecture_id == lecture_id,
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                raise
        else:
            await db.refresh(existing)

    return _to_favourite_response(existing, lecture)


@router.delete("/{lecture_id}", status_code=status.HTTP_200_OK)
async def remove_from_favourites(
    lecture_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, str]:
    favourite = (
        await db.execute(
            select(Favourite).where(
                Favourite.user_id == user.id,
                Favourite.lecture_id == lecture_id,
            )
        )
    ).scalar_one_or_none()
    if favourite is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Favourite not found")

    await db.delete(favourite)
    await db.commit()
    return {"status": "deleted", "lecture_id": str(lecture_id)}
