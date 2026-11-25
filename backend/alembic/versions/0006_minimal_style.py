"""v0.12: убраны «огоньки» (серии дней в чатах); цвет акцента интерфейса — blue | green | purple

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # старые названия тем (plasma, neon, ...) → новый акцент по умолчанию
    op.execute("UPDATE users SET theme = 'purple' WHERE theme NOT IN ('blue', 'green', 'purple')")
    with op.batch_alter_table("chats") as b:
        for col in ("streak", "streak_date", "last_day_a", "last_day_b"):
            b.drop_column(col)


def downgrade() -> None:
    with op.batch_alter_table("chats") as b:
        b.add_column(sa.Column("streak", sa.Integer, nullable=False, server_default="0"))
        b.add_column(sa.Column("streak_date", sa.String(10)))
        b.add_column(sa.Column("last_day_a", sa.String(10)))
        b.add_column(sa.Column("last_day_b", sa.String(10)))
