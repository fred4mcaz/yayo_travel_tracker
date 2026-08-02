"""merge_dismissal: trips deliberately kept separate

Records the persistent opposite of a merge, so mergeable_trips stops
re-proposing a same-country, near-dated pair the user has already waved off.

Revision ID: d3e8b1a94c27
Revises: f2a7c9d34b58
Create Date: 2026-08-02 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d3e8b1a94c27"
down_revision: Union[str, None] = "f2a7c9d34b58"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "merge_dismissal",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("trip_low_id", sa.Integer(), nullable=False),
        sa.Column("trip_high_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        # Either trip going away -- deleted, or absorbed by a merge -- drops the
        # dismissal, so a stale pair can never linger.
        sa.ForeignKeyConstraint(["trip_low_id"], ["trip.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["trip_high_id"], ["trip.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "trip_low_id", "trip_high_id", name="uq_merge_dismissal_pair"
        ),
    )
    with op.batch_alter_table("merge_dismissal", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_merge_dismissal_trip_low_id"),
            ["trip_low_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_merge_dismissal_trip_high_id"),
            ["trip_high_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("merge_dismissal", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_merge_dismissal_trip_high_id"))
        batch_op.drop_index(batch_op.f("ix_merge_dismissal_trip_low_id"))
    op.drop_table("merge_dismissal")
