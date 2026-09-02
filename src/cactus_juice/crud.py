from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from cactus_juice.model import CSIPAusControl


async def fetch_controls_active_from(
    session: AsyncSession, epoch: datetime, start: int = 0, limit: int = 500
) -> Sequence[CSIPAusControl]:
    """Fetches all CSIPAusControls that are active from this specified epoch. Will consider cancelled/superseded times
    (a control whose finish time is after epoch BUT their cancellation/superseded time is BEFORE epoch will be excluded)

    This is designed to be used with an epoch close to "now"

    Returns them ordered by start_time ASC."""

    stmt = (
        select(CSIPAusControl)
        .where(CSIPAusControl.finished_at > epoch)  # This clause will do the heavy lifting for filtering results
        .where(or_(CSIPAusControl.superseded_at.is_(None), CSIPAusControl.superseded_at > epoch))
        .where(or_(CSIPAusControl.cancelled_at.is_(None), CSIPAusControl.cancelled_at > epoch))
        .order_by(CSIPAusControl.started_at.asc())
        .offset(start)
        .limit(limit)
    )

    return (await session.execute(stmt)).scalars().all()
