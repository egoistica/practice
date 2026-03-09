from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.history import History


async def record_history_visit(
    db: AsyncSession,
    user_id: uuid.UUID,
    lecture_id: uuid.UUID,
) -> bool:
    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)

    existing_id = await db.scalar(
        select(History.id).where(
            History.user_id == user_id,
            History.lecture_id == lecture_id,
            History.visited_at >= day_start,
            History.visited_at < day_end,
        )
    )
    if existing_id is not None:
        await db.execute(
            update(History).where(History.id == existing_id).values(visited_at=now)
        )
        return True

    try:
        async with db.begin_nested():
            db.add(
                History(
                    user_id=user_id,
                    lecture_id=lecture_id,
                    visited_at=now,
                )
            )
            await db.flush()
        return True
    except IntegrityError:
        return False
