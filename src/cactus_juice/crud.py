from collections.abc import Iterable, Sequence
from datetime import UTC, datetime

from sqlalchemy import and_, func, insert, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from cactus_juice.csipaus.dto import HasDefaultValues
from cactus_juice.model import CSIPAusControl, CSIPAusControlResponse, CSIPAusDefault, OCPPMetadata, OCPPReading

DEFAULT_MAX_DATE = datetime(9999, 1, 1, tzinfo=UTC)


async def fetch_controls_with_mrids(
    session: AsyncSession, mrids: Iterable[str], start: int = 0, limit: int = 500
) -> Sequence[CSIPAusControl]:
    """Fetches all CSIPAusControl with the specified mRID values. Returns them ordered by PK"""
    stmt = (
        select(CSIPAusControl)
        .where(CSIPAusControl.mrid.in_(mrids))  # This clause will do the heavy lifting for filtering results
        .order_by(CSIPAusControl.csipaus_control_id.asc())
        .offset(start)
        .limit(limit)
    )

    return (await session.execute(stmt)).scalars().all()


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
    (csipaus_control_id, end_device_lfdi, response_status) unique constraint, the existing record is updated
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
        "end_device_lfdi",
        "not_before",
        "sent_at",
    )

    values = [{col: getattr(r, col) for col in insert_columns} for r in responses]

    # bulk INSERT ... ON CONFLICT (...) DO UPDATE.
    insert_stmt = pg_insert(CSIPAusControlResponse).values(values)
    excluded = insert_stmt.excluded

    stmt = insert_stmt.on_conflict_do_update(
        index_elements=["csipaus_control_id", "end_device_lfdi", "response_status"],
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

    A record is active when now falls within [started_at, finished_at) (matching the active_range '[)' bounds)."""
    stmt = (
        select(CSIPAusDefault)
        .where(CSIPAusDefault.started_at <= now)
        .where(CSIPAusDefault.finished_at > now)
        # The overlap exclusion constraint guarantees at most one match - order/limit are just belt and braces.
        .order_by(CSIPAusDefault.started_at.desc())
        .limit(1)
    )

    return (await session.execute(stmt)).scalars().one_or_none()


async def fetch_defaults_from(
    session: AsyncSession, now: datetime, start: int = 0, limit: int = 500
) -> Sequence[CSIPAusDefault]:
    """Fetches all CSIPAusDefaults that are active from now.

    A record is active when now falls within [started_at, finished_at).

    returns records ordered by their active time ASC"""
    stmt = (
        select(CSIPAusDefault)
        .where(CSIPAusDefault.finished_at > now)
        .order_by(CSIPAusDefault.started_at.asc())
        .offset(start)
        .limit(limit)
    )

    return (await session.execute(stmt)).scalars().all()


async def update_active_default(session: AsyncSession, now: datetime, values: HasDefaultValues) -> None:
    """Inserts a new CSIPAusDefault record that is started_at now until DEFAULT_MAX_DATE - any existing default records
    that intersect this new range will have their finished_at updated to now.

    The intent is to always maintain a rolling history of the "active" defaults through time

    If the currently active default already carries exactly these values this is a no-op - we don't want to
    fragment the history with records that don't actually change anything.

    does NOT commit any transaction."""

    # The default value columns on CSIPAusDefault - i.e. everything a HasDefaultValues carries. Excludes the
    # PK and the started_at/finished_at (+ computed active_range) window columns.
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

    # Nothing to do if the active default is already carrying these exact values.
    current = await fetch_active_default(session, now)
    if current is not None and all(getattr(current, col) == getattr(values, col) for col in default_value_columns):
        return

    # Close off any record still active at (or beyond) now. The started_at <= now guard keeps us from
    # inverting the range of a future-dated record - if one somehow exists the overlap exclusion
    # constraint will reject the insert below rather than silently corrupting the history.
    await session.execute(
        update(CSIPAusDefault)
        .where(CSIPAusDefault.started_at <= now)
        .where(CSIPAusDefault.finished_at > now)
        .values(finished_at=now)
    )

    await session.execute(
        insert(CSIPAusDefault).values(
            started_at=now,
            finished_at=DEFAULT_MAX_DATE,
            **{col: getattr(values, col) for col in default_value_columns},
        )
    )


async def fetch_ocpp_readings_in_range(
    session: AsyncSession, readings_from: datetime, readings_to: datetime, start: int = 0, limit: int = 500
) -> Sequence[OCPPReading]:
    """Fetches all OCPPReadings that exist in [readings_from, readings_to) (inclusive to exclusive).

    Readings will be ordered by reading_start ASC"""

    return (
        (
            await session.execute(
                select(OCPPReading)
                .where((OCPPReading.reading_start >= readings_from) & (OCPPReading.reading_start < readings_to))
                .order_by(OCPPReading.reading_start, OCPPReading.ocpp_reading_id)
                .offset(start)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )


async def fetch_ocpp_metadata(session: AsyncSession) -> OCPPMetadata | None:
    """Fetches the latest OCPPMetadata or None if none has been registered yet"""

    return (
        await session.execute(select(OCPPMetadata).order_by(OCPPMetadata.created_at.desc()).limit(1))
    ).scalar_one_or_none()
