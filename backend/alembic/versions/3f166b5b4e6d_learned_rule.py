"""learned_rule: sender domains the operator taught the filter at runtime

The committed data/rules/email-filter.json is read-only and baked into the
image; this table is where a domain learned through the review page's
manual-extract-then-accept flow (phase 4) actually lives. email_filter's
effective_rules() unions the two at classification time.

Revision ID: 3f166b5b4e6d
Revises: d3e8b1a94c27
Create Date: 2026-08-04 08:37:55.651747
"""

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "3f166b5b4e6d"
down_revision: Union[str, None] = "d3e8b1a94c27"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "learned_rule",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("domain", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("source", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("learned_rule", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_learned_rule_domain"), ["domain"], unique=True
        )


def downgrade() -> None:
    with op.batch_alter_table("learned_rule", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_learned_rule_domain"))
    op.drop_table("learned_rule")
