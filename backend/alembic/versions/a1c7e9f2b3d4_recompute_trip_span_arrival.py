"""recompute each trip's span so a flight starts it on arrival, not departure

Revision ID: a1c7e9f2b3d4
Revises: 50cd760e587a
Create Date: 2026-09-16 12:00:00.000000

The denormalised trip.start_date / end_date are only rewritten when a trip is
touched, so trips stored before the arrival-vs-departure fix keep their old
span -- the Kazakhstan trip still began on the London *departure* day. This
one-time backfill recomputes every trip's span with the corrected rule (a leg
counts as the day it lands, `arrive_at` else `depart_at`), matching
refresh_trip_dates so the calendar and everything else agree at once.

Data-only. Dates are ISO 'YYYY-MM-DD...' text, so substr(...,1,10) is the date
half and MIN/MAX over that text sorts chronologically. Irreversible in any
meaningful sense: the previous (wrong) values are not worth restoring, and any
later edit recomputes the span anyway.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "a1c7e9f2b3d4"
down_revision: Union[str, None] = "50cd760e587a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # start = earliest of every stay check-in and every leg's landing day.
    op.execute(
        """
        UPDATE trip SET start_date = (
            SELECT MIN(d) FROM (
                SELECT check_in AS d FROM stay WHERE stay.trip_id = trip.id
                UNION ALL
                SELECT substr(COALESCE(arrive_at, depart_at), 1, 10) AS d
                    FROM leg
                    WHERE leg.trip_id = trip.id
                      AND COALESCE(arrive_at, depart_at) IS NOT NULL
            )
        )
        """
    )
    # end = latest of every checkout, every leg's landing day, and the recorded
    # leaving date -- the stay is not over when the last hotel ends.
    op.execute(
        """
        UPDATE trip SET end_date = (
            SELECT MAX(d) FROM (
                SELECT check_out AS d FROM stay WHERE stay.trip_id = trip.id
                UNION ALL
                SELECT substr(COALESCE(arrive_at, depart_at), 1, 10) AS d
                    FROM leg
                    WHERE leg.trip_id = trip.id
                      AND COALESCE(arrive_at, depart_at) IS NOT NULL
                UNION ALL
                SELECT exited_on AS d FROM country_entry
                    WHERE country_entry.trip_id = trip.id
                      AND exited_on IS NOT NULL
            )
        )
        """
    )


def downgrade() -> None:
    # The old values were wrong; recomputing them is exactly what this fixed.
    pass
