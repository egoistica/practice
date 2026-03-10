"""add realtime_mode column to lecture

Revision ID: 4e0f9c4b1a2d
Revises: 00653f1a4ae1
Create Date: 2026-03-11 14:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "4e0f9c4b1a2d"
down_revision: Union[str, None] = "00653f1a4ae1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "lecture",
        sa.Column("realtime_mode", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("lecture", "realtime_mode")

