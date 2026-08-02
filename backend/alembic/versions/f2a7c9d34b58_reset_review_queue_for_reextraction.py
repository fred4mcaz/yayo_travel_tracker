"""reset the review queue so old mail is re-read with the year fix

Revision ID: f2a7c9d34b58
Revises: ba5dfa0e58f9
Create Date: 2026-08-02 12:00:00.000000

A one-time clean slate. Early extractions were produced before the extractor
knew what year it was (it read Aug 2026 as Aug 2025), and dismissing one only
marked its proposal rejected -- the email stayed processed and never came back.
This drops every proposal that was not accepted into a trip and re-arms its
email, so the next poll re-reads it through the corrected extractor and it
reappears in Review with the right dates. Accepted trips are left untouched.

Data-only, and irreversible: the deleted proposals cannot be un-deleted, but
they are cheaply re-created by the next extraction pass, which is the point.
"""

from typing import Sequence, Union

from alembic import op

revision: str = 'f2a7c9d34b58'
down_revision: Union[str, None] = 'ba5dfa0e58f9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop every proposal that never became a trip. Accepted ones stay: their
    # trip data is real and re-reading their email would only duplicate it.
    op.execute("DELETE FROM extraction WHERE status != 'accepted'")

    # Re-arm every travel-candidate email that is not tied to an accepted
    # proposal, so run_extractions picks it up again on the next poll. An email
    # with an accepted proposal keeps its processed stamp and is never re-read.
    op.execute(
        "UPDATE email_message SET processed_at = NULL "
        "WHERE looks_like_travel = 1 "
        "AND id NOT IN ("
        "SELECT email_message_id FROM extraction WHERE status = 'accepted'"
        ")"
    )


def downgrade() -> None:
    # A data reset has no meaningful inverse -- the dropped proposals are gone,
    # and re-marking emails processed would just hide mail that has not been
    # re-read yet. Nothing to do.
    pass
