from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from cactus_juice.model import CSIPAusControl, CSIPAusControlResponse


async def fetch_controls_active_from(
    session: AsyncSession, epoch: datetime, start: int = 0, limit: int = 500
) -> Sequence[CSIPAusControl]:
    """Fetches all CSIPAusControls that are active from this specified epoch. Will consider cancelled/superseded times
    (a control whose finish time is after epoch BUT their cancellation/superseded time is BEFORE epoch will be excluded)

    This is designed to be used with an epoch close to "now"

    Returns them ordered by start_time ASC, id ASC."""

    stmt = (
        select(CSIPAusControl)
        .where(CSIPAusControl.finished_at > epoch)  # This clause will do the heavy lifting for filtering results
        .where(or_(CSIPAusControl.superseded_at.is_(None), CSIPAusControl.superseded_at > epoch))
        .where(or_(CSIPAusControl.cancelled_at.is_(None), CSIPAusControl.cancelled_at > epoch))
        .order_by(CSIPAusControl.started_at.asc(), CSIPAusControl.csipaus_control_id.asc())
        .offset(start)
        .limit(limit)
    )

    return (await session.execute(stmt)).scalars().all()


async def upsert_controls(session: AsyncSession, controls: list[CSIPAusControl]) -> None:
    """Inserts the specified set of controls - if there is a conflict on mRID, the existing records updated following
    these rules:
        1) ONLY the existing cancelled_at/superseded_at values can be updated
        2) The cancelled_at/superseded_at values will ONLY update if they are currently NULL (no updating a set value)

    does NOT commit any transaction."""
    if not controls:
        return

    # Excludes PK and computed/default cols
    insert_columns = (
        "primacy",
        "mrid",
        "duration_seconds",
        "started_at",
        "cancelled_at",
        "superseded_at",
        "ramp_time_seconds",
        "connect",
        "energize",
        "import_limit_watts",
        "export_limit_watts",
        "load_limit_watts",
        "generation_limit_watts",
        "storage_target_watts",
    )

    values = [{col: getattr(c, col) for col in insert_columns} for c in controls]

    # Single round-trip: bulk INSERT ... ON CONFLICT (mrid) DO UPDATE.
    insert_stmt = pg_insert(CSIPAusControl).values(values)
    excluded = insert_stmt.excluded

    # We dont want to overwrite existing non null values so we lean on coalesce
    stmt = insert_stmt.on_conflict_do_update(
        index_elements=["mrid"],
        set_={
            "cancelled_at": func.coalesce(CSIPAusControl.cancelled_at, excluded.cancelled_at),
            "superseded_at": func.coalesce(CSIPAusControl.superseded_at, excluded.superseded_at),
        },
        where=or_(
            and_(CSIPAusControl.cancelled_at.is_(None), excluded.cancelled_at.is_not(None)),
            and_(CSIPAusControl.superseded_at.is_(None), excluded.superseded_at.is_not(None)),
        ),  # ONLY update where there is actually something to update
    )

    await session.execute(stmt)


async def fetch_unsent_control_responses(
    session: AsyncSession, now: datetime, start: int = 0, limit: int = 500
) -> Sequence[CSIPAusControlResponse]:
    """Fetches all CSIPAusControlResponse which are due to send (according to now)"""
    stmt = (
        select(CSIPAusControlResponse)
        .where(CSIPAusControlResponse.sent_at.is_not(None))
        .where(CSIPAusControlResponse.not_before < now)
        .order_by(CSIPAusControlResponse.csipaus_control_response_id.asc())
        .offset(start)
        .limit(limit)
    )

    return (await session.execute(stmt)).scalars().all()
