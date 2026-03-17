"""add idempotency key to token transaction

Revision ID: 5f0a7c3b9e21
Revises: 4e0f9c4b1a2d
Create Date: 2026-03-17 15:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "5f0a7c3b9e21"
down_revision: Union[str, None] = "4e0f9c4b1a2d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "token_transaction",
        sa.Column("idempotency_key", sa.String(length=191), nullable=True),
    )
    op.create_unique_constraint(
        "uq_token_transaction_user_idempotency_key",
        "token_transaction",
        ["user_id", "idempotency_key"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_token_transaction_user_idempotency_key",
        "token_transaction",
        type_="unique",
    )
    op.drop_column("token_transaction", "idempotency_key")
