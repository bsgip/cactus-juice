from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import and_, func, insert, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from cactus_juice.csipaus.dto import HasDefaultValues
from cactus_juice.model import CSIPAusControl, CSIPAusControlResponse, CSIPAusDefault

DEFAULT_MAX_DATE = datetime(9999, 1, 1, tzinfo=UTC)


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


async def upsert_control_responses(session: AsyncSession, responses: list[CSIPAusControlResponse]) -> None:
    """Inserts the specified set of control responses - if there is a conflict on the
    (csipaus_control_id, end_device_mrid, response_status) unique constraint, the existing record is updated
    following these rules:
        1) ONLY the existing sent_at value can be updated
        2) sent_at will ONLY update if the existing row's sent_at is currently NULL

    does NOT commit any transaction."""
    if not responses:
        return

    # Excludes PK and computed/default cols
    insert_columns = (
        "csipaus_control_id",
        "response_status",
        "end_device_mrid",
        "not_before",
        "sent_at",
    )

    values = [{col: getattr(r, col) for col in insert_columns} for r in responses]

    # bulk INSERT ... ON CONFLICT (...) DO UPDATE.
    insert_stmt = pg_insert(CSIPAusControlResponse).values(values)
    excluded = insert_stmt.excluded

    stmt = insert_stmt.on_conflict_do_update(
        index_elements=["csipaus_control_id", "end_device_mrid", "response_status"],
        set_={"sent_at": excluded.sent_at},
        where=CSIPAusControlResponse.sent_at.is_(None),  # ONLY touch responses that haven't been sent yet
    )

    await session.execute(stmt)


async def fetch_unsent_control_responses(
    session: AsyncSession, now: datetime, start: int = 0, limit: int = 500
) -> Sequence[CSIPAusControlResponse]:
    """Fetches all CSIPAusControlResponse which are due to send (according to now)

    Returns ordered by the PK ASC"""
    stmt = (
        select(CSIPAusControlResponse)
        .where(CSIPAusControlResponse.sent_at.is_(None))
        .where(CSIPAusControlResponse.not_before >= now)
        .order_by(CSIPAusControlResponse.csipaus_control_response_id.asc())
        .offset(start)
        .limit(limit)
    )

    return (await session.execute(stmt)).scalars().all()


async def fetch_active_default(session: AsyncSession, now: datetime) -> CSIPAusDefault | None:
    """Fetches the CSIPAusDefault that is active at 'now' (or None if there is none available).

    A record is active when now falls within [active_from, active_to) (matching the active_range '[)' bounds)."""
    stmt = (
        select(CSIPAusDefault)
        .where(CSIPAusDefault.active_from <= now)
        .where(CSIPAusDefault.active_to > now)
        # The overlap exclusion constraint guarantees at most one match - order/limit are just belt and braces.
        .order_by(CSIPAusDefault.active_from.desc())
        .limit(1)
    )

    return (await session.execute(stmt)).scalars().one_or_none()


async def update_active_default(session: AsyncSession, now: datetime, values: HasDefaultValues) -> None:
    """Inserts a new CSIPAusDefault record that is active_from now until DEFAULT_MAX_DATE - any existing default records
    that intersect this new range will have their active_to updated to now.

    The intent is to always maintain a rolling history of the "active" defaults through time

    does NOT commit any transaction."""

    # Close off any record still active at (or beyond) now. The active_from <= now guard keeps us from
    # inverting the range of a future-dated record - if one somehow exists the overlap exclusion
    # constraint will reject the insert below rather than silently corrupting the history.
    await session.execute(
        update(CSIPAusDefault)
        .where(CSIPAusDefault.active_from <= now)
        .where(CSIPAusDefault.active_to > now)
        .values(active_to=now)
    )

    # The default value columns on CSIPAusDefault - i.e. everything a HasDefaultValues carries. Excludes the
    # PK and the active_from/active_to (+ computed active_range) window columns.
    default_value_columns = (
        "ramp_percent_max_second_hundredths",
        "connect",
        "energize",
        "import_limit_watts",
        "export_limit_watts",
        "load_limit_watts",
        "generation_limit_watts",
        "storage_target_watts",
    )

    await session.execute(
        insert(CSIPAusDefault).values(
            active_from=now,
            active_to=DEFAULT_MAX_DATE,
            **{col: getattr(values, col) for col in default_value_columns},
        )
    )
