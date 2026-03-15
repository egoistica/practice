from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime
from typing import Iterator

from sqlalchemy import BigInteger, DateTime, String, Text, create_engine, func
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class TelegramUser(Base):
    __tablename__ = "telegram_user"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    jwt_token: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


def _database_url() -> str:
    return os.getenv("TELEGRAM_BOT_DATABASE_URL", "sqlite:///./telegram_bot.sqlite3").strip()


def _engine():
    url = _database_url()
    connect_args = {"check_same_thread": False} if url.startswith("sqlite:///") else {}
    return create_engine(url, pool_pre_ping=True, future=True, connect_args=connect_args)


engine = _engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
