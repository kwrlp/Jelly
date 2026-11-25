"""v0.12.1: медуза-питомец чатов удалена

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("chats") as b:
        b.drop_column("pet_xp")


def downgrade() -> None:
    with op.batch_alter_table("chats") as b:
        b.add_column(sa.Column("pet_xp", sa.Integer, nullable=False, server_default="0"))
