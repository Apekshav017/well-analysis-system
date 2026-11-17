"""v2: app settings, cancelled runs, well locations, learning flags, administrator notes

Revision ID: a4c7e2d19b5f
Revises: 98ebf4fa741d
Create Date: 2026-10-28 10:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a4c7e2d19b5f"
down_revision: Union[str, Sequence[str], None] = "98ebf4fa741d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"],
                                name=op.f("fk_app_settings_updated_by_users"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_app_settings")),
    )
    op.add_column("detected_wells", sa.Column("village", sa.String(length=255), nullable=True))
    op.add_column("detected_wells", sa.Column("taluka", sa.String(length=255), nullable=True))
    op.add_column("detected_wells", sa.Column("district", sa.String(length=255), nullable=True))
    op.add_column("detected_wells", sa.Column("learned_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("known_wells", sa.Column("notes", sa.Text(), nullable=True))
    op.drop_constraint(op.f("ck_detection_jobs_status_valid"), "detection_jobs", type_="check")
    op.create_check_constraint(
        op.f("ck_detection_jobs_status_valid"), "detection_jobs",
        "status IN ('pending', 'running', 'cancelled', 'completed', 'failed')",
    )


def downgrade() -> None:
    op.execute("UPDATE detection_jobs SET status = 'failed' WHERE status = 'cancelled'")
    op.drop_constraint(op.f("ck_detection_jobs_status_valid"), "detection_jobs", type_="check")
    op.create_check_constraint(
        op.f("ck_detection_jobs_status_valid"), "detection_jobs",
        "status IN ('pending', 'running', 'completed', 'failed')",
    )
    op.drop_column("known_wells", "notes")
    op.drop_column("detected_wells", "learned_at")
    op.drop_column("detected_wells", "district")
    op.drop_column("detected_wells", "taluka")
    op.drop_column("detected_wells", "village")
    op.drop_table("app_settings")
