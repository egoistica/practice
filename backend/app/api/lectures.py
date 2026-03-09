from __future__ import annotations

import json
import logging
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.dependencies import get_current_user, get_db
from app.models.lecture import Lecture, LectureMode, LectureSourceType, LectureStatus
from app.models.user import User
from app.schemas.lecture import CreateLectureRequest, LectureListResponse, LectureResponse
from app.services.file_service import delete_lecture_media, save_uploaded_file

router = APIRouter(prefix="/lectures", tags=["lectures"])
logger = logging.getLogger(__name__)


def _to_lecture_response(lecture: Lecture) -> LectureResponse:
    return LectureResponse(
        id=lecture.id,
        title=lecture.title,
        status=str(lecture.status.value if hasattr(lecture.status, "value") else lecture.status),
        processing_progress=lecture.processing_progress,
        created_at=lecture.created_at,
    )


def _parse_selected_entities(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None

    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        decoded = [part.strip() for part in value.split(",") if part.strip()]
        return decoded or None

    if decoded is None:
        return None
    if not isinstance(decoded, list) or not all(isinstance(item, str) for item in decoded):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="selected_entities must be a JSON array of strings or comma-separated string",
        )
    normalized = [item.strip() for item in decoded if item.strip()]
    return normalized or None


async def parse_create_lecture_request(
    title: str = Form(...),
    mode: LectureMode = Form(default=LectureMode.INSTANT),
    source_type: LectureSourceType = Form(...),
    source_url: str | None = Form(default=None),
    selected_entities: str | None = Form(default=None),
) -> CreateLectureRequest:
    normalized_title = title.strip()
    normalized_source_url = source_url.strip() if source_url else None
    entities = _parse_selected_entities(selected_entities)

    try:
        return CreateLectureRequest(
            title=normalized_title,
            mode=mode,
            source_type=source_type,
            source_url=normalized_source_url,
            selected_entities=entities,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("", response_model=LectureResponse, status_code=status.HTTP_201_CREATED)
async def create_lecture(
    payload: CreateLectureRequest = Depends(parse_create_lecture_request),
    file: UploadFile | None = File(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> LectureResponse:
    lecture_id = uuid.uuid4()
    file_path: str | None = None
    source_url: str | None = str(payload.source_url) if payload.source_url else None
    if file is not None and not file.filename:
        file = None

    if payload.source_type == LectureSourceType.FILE:
        if file is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File is required when source_type=file",
            )
        if source_url:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="source_url must be empty when source_type=file",
            )
        try:
            file_path = await save_uploaded_file(file, settings.MEDIA_ROOT, lecture_id)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    elif payload.source_type == LectureSourceType.URL:
        if not source_url:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="source_url is required when source_type=url",
            )
        if file is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File must not be provided when source_type=url",
            )
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported source_type")

    lecture = Lecture(
        id=lecture_id,
        user_id=user.id,
        title=payload.title,
        mode=payload.mode,
        source_type=payload.source_type,
        source_url=source_url,
        file_path=file_path,
        status=LectureStatus.PENDING,
        processing_progress=0,
    )
    db.add(lecture)
    await db.commit()
    await db.refresh(lecture)

    if payload.selected_entities:
        logger.info(
            "lecture_selected_entities lecture_id=%s user_id=%s entities=%s",
            lecture.id,
            user.id,
            payload.selected_entities,
        )

    return _to_lecture_response(lecture)


@router.get("", response_model=LectureListResponse)
async def list_lectures(
    skip: int = 0,
    limit: int = 20,
    sort_order: Literal["asc", "desc"] = "desc",
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> LectureListResponse:
    if skip < 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="skip must be >= 0")
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="limit must be between 1 and 100")

    order_clause = Lecture.created_at.asc() if sort_order == "asc" else Lecture.created_at.desc()
    items_result = await db.execute(
        select(Lecture)
        .where(Lecture.user_id == user.id)
        .order_by(order_clause)
        .offset(skip)
        .limit(limit)
    )
    lectures = items_result.scalars().all()
    total = int(
        (
            await db.execute(
                select(func.count()).select_from(Lecture).where(Lecture.user_id == user.id)
            )
        ).scalar_one()
    )

    return LectureListResponse(
        items=[_to_lecture_response(item) for item in lectures],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.get("/{lecture_id}", response_model=LectureResponse)
async def get_lecture(
    lecture_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> LectureResponse:
    lecture = await db.get(Lecture, lecture_id)
    if lecture is None or lecture.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lecture not found")
    return _to_lecture_response(lecture)


@router.delete("/{lecture_id}", status_code=status.HTTP_200_OK)
async def delete_lecture(
    lecture_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, str]:
    lecture = await db.get(Lecture, lecture_id)
    if lecture is None or lecture.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lecture not found")

    await db.delete(lecture)
    await db.commit()

    try:
        delete_lecture_media(settings.MEDIA_ROOT, lecture_id)
    except OSError:
        logger.exception("Failed at media deletion stage for lecture_id=%s", lecture_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete lecture media",
        ) from None

    return {"status": "deleted", "lecture_id": str(lecture_id)}
