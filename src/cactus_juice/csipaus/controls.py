import heapq
from bisect import bisect_left
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from cactus_juice.crud import DEFAULT_MAX_DATE, fetch_controls_active_from, fetch_defaults_from
from cactus_juice.csipaus.dto import DefaultValues, HasDefaultValues, ScheduledControlValues
from cactus_juice.model import CSIPAusControl, CSIPAusDefault


@dataclass(frozen=True, slots=True)
class IntervalBoundary:
    """Represents a moment in time where some controls/defaults started/finished"""

    moment: datetime
    started_controls: list[CSIPAusControl]  # Controls that start at this moment (expect 0 to N)
    started_defaults: list[CSIPAusDefault]  # defaults that start at this moment (expect 0 or 1)

    finished_controls: list[CSIPAusControl]  # Controls that end at this moment (expect 0 to N)
    finished_defaults: list[CSIPAusDefault]  # defaults that end at this moment (expect 0 or 1)


@dataclass(frozen=True, slots=True)
class ActiveInterval:
    """Represents a chunk of time with a specific set of controls/defaults that apply for that interval"""

    active_from: datetime
    active_to: datetime | None
    active_controls: list[CSIPAusControl]  # Controls that overlap with this interval
    active_default: HasDefaultValues  # Default that overlaps with this interval


def generate_default_default() -> HasDefaultValues:
    """Generates the implied default values that always exist (even if there are none on record)"""
    return DefaultValues(None, None, None, None, None, None, None, None)


DEFAULT_DEFAULT = generate_default_default()


def get_control_finished_at(c: CSIPAusControl) -> datetime:
    finished_at = c.finished_at
    if c.cancelled_at:
        finished_at = min(finished_at, c.cancelled_at)
    if c.superseded_at:
        finished_at = min(finished_at, c.superseded_at)
    return finished_at


def drain_intermediate_end_boundaries(
    new_boundary: datetime,
    upcoming_end_boundaries: list[tuple[datetime, CSIPAusDefault | None, CSIPAusControl | None]],
) -> tuple[list[IntervalBoundary], IntervalBoundary]:
    """Given upcoming_end_boundaries - remove entries from the start and return an IntervalBoundary for EVERY
    unique "finished_at" time. Will also create the new IntervalBoundary for new_boundary"

    upcoming_end_boundaries: list[tuple(finished_at, default, control)] - Must be sorted by finished_at

    returns all intermediate_end_boundaries AND a IntervalBoundary for new_boundary (not included in the list)
    """
    current_end_boundary: IntervalBoundary | None = None

    intermediate_boundaries: list[IntervalBoundary] = []
    while len(upcoming_end_boundaries) and upcoming_end_boundaries[0][0] <= new_boundary:
        next_end, d, c = upcoming_end_boundaries.pop(0)

        # Is this a fresh end_time or does it match the end we are currently building
        if current_end_boundary is None or current_end_boundary.moment != next_end:
            if current_end_boundary is not None:
                intermediate_boundaries.append(current_end_boundary)
            current_end_boundary = IntervalBoundary(
                next_end, started_controls=[], started_defaults=[], finished_controls=[], finished_defaults=[]
            )

        if d is not None:
            current_end_boundary.finished_defaults.append(d)
        if c is not None:
            current_end_boundary.finished_controls.append(c)

    if current_end_boundary is not None:
        intermediate_boundaries.append(current_end_boundary)

    # We may / may not have entities finishing at the start of the new boundary
    if len(intermediate_boundaries) == 0 or intermediate_boundaries[-1].moment != new_boundary:
        return (intermediate_boundaries, IntervalBoundary(new_boundary, [], [], [], []))
    else:
        # Don't include the new_boundary Interval in the list
        return (intermediate_boundaries, intermediate_boundaries.pop())


def generate_interval_boundaries(
    now: datetime, defaults: Iterable[CSIPAusDefault], controls: Iterable[CSIPAusControl]
) -> list[IntervalBoundary]:
    """Given defaults and controls sorted by their started_at times (ASC) - group them into common/shared interval
    boundaries showing all the (potentially common) moments that controls/defaults start/finish. Each entity will
    start and finish exactly once each.

    Any started_at time < now will be treated as now

    Avoid having any default/control has a finished_at <= now - this will have undefined results - ensure such entries
    are filtered before calling this function

    Resulting list will be sorted in ASC order.
    """

    # Anything that precedes "now" should be clamped to now
    clamped_defaults = ((max(now, d.started_at), d, None) for d in defaults)
    clamped_controls = ((max(now, c.started_at), None, c) for c in controls)

    # The start boundaries can be done efficiently from taking from either controls/defaults in sort order
    current_start_boundary = IntervalBoundary(now, [], [], [], [])
    all_start_boundaries: list[IntervalBoundary] = []
    upcoming_end_boundaries: list[tuple[datetime, CSIPAusDefault | None, CSIPAusControl | None]] = []

    for start, d, c in heapq.merge(clamped_defaults, clamped_controls, key=lambda e: e[0]):
        # If we have identified a new starting boundary, close out the current boundary AND inject any intermediate
        # end boundaries
        if start != current_start_boundary.moment:
            # We have jumped to a new boundary - there might have been some endings between previous and now
            intermediate_boundaries, next_start_boundary = drain_intermediate_end_boundaries(
                start, upcoming_end_boundaries
            )

            all_start_boundaries.append(current_start_boundary)
            if intermediate_boundaries:
                all_start_boundaries.extend(intermediate_boundaries)
            current_start_boundary = next_start_boundary

        # Add this entity into the "started" list as this is the beginning of it
        # Also - add a sorted entry into upcoming_end_boundaries for its finished time
        if d is not None:
            current_start_boundary.started_defaults.append(d)
            end_entry = (d.finished_at, d, None)
            insert_location = bisect_left(upcoming_end_boundaries, end_entry[0], key=lambda e: e[0])
            upcoming_end_boundaries.insert(insert_location, end_entry)
        if c is not None:
            current_start_boundary.started_controls.append(c)
            end_entry = (get_control_finished_at(c), None, c)
            insert_location = bisect_left(upcoming_end_boundaries, end_entry[0], key=lambda e: e[0])
            upcoming_end_boundaries.insert(insert_location, end_entry)

    all_start_boundaries.append(current_start_boundary)

    # drain the upcoming end boundaries to ensure we capture each future finish
    intermediate_boundaries, final_boundary = drain_intermediate_end_boundaries(
        DEFAULT_MAX_DATE, upcoming_end_boundaries
    )
    if intermediate_boundaries:
        all_start_boundaries.extend(intermediate_boundaries)
    all_start_boundaries.append(final_boundary)

    return all_start_boundaries


def generate_intervals(
    now: datetime, defaults: Iterable[CSIPAusDefault], controls: Iterable[CSIPAusControl]
) -> list[ActiveInterval]:
    """Given a set of defaults/controls - group them into common time intervals. Result will be sorted by active_from
    and contain no gaps. Final interval will have active_to be None. A default default is considered to always be in
    effect if no default exists for a period of time

    now is used to artifically clamp any controls/defaults that started in the past to this time.

    defaults - must be sorted by active_from ASC
    controls - must be sorted by started_at ASC"""

    def _new_active_interval(
        active_from: datetime,
        active_to: datetime | None,
        controls: dict[int, CSIPAusControl],
        defaults: dict[int, CSIPAusDefault],
    ) -> ActiveInterval:
        all_defaults = list(defaults.values())  # this should be 0 or 1 entities
        if len(all_defaults) > 1:
            raise ValueError(f"Found {len(all_defaults)} defaults overlapping in range [{active_from=} {active_to=})")
        return ActiveInterval(
            active_from=active_from,
            active_to=active_to,
            active_controls=list(controls.values()),
            active_default=defaults[0] if defaults else DEFAULT_DEFAULT,
        )

    active_controls_by_id: dict[int, CSIPAusControl] = {}
    active_defaults_by_id: dict[int, CSIPAusDefault] = {}
    resulting_active_intervals: list[ActiveInterval] = []
    previous_interval: datetime | None = None
    for current_interval in generate_interval_boundaries(now, defaults, controls):
        # Close out an interval
        if previous_interval is not None:
            resulting_active_intervals.append(
                _new_active_interval(
                    active_from=previous_interval,
                    active_to=current_interval.moment,
                    controls=active_controls_by_id,
                    defaults=active_defaults_by_id,
                )
            )

        # As we hit a new interval boundary - add/remove the newly started/finished controls/defaults
        for c in current_interval.started_controls:
            active_controls_by_id[c.csipaus_control_id] = c
        for d in current_interval.started_defaults:
            active_defaults_by_id[d.csipaus_default_id] = d

        for c in current_interval.finished_controls:
            del active_controls_by_id[c.csipaus_control_id]
        for d in current_interval.finished_defaults:
            del active_defaults_by_id[d.csipaus_default_id]

        # Swap to the new interval
        previous_interval = current_interval.moment

    # We finally need to inject a value from where we finished to infinity
    if len(resulting_active_intervals) == 0:
        resulting_active_intervals.append(
            _new_active_interval(
                active_from=now,
                active_to=None,
                controls=active_controls_by_id,
                defaults=active_defaults_by_id,
            )
        )
    else:
        last_finish = resulting_active_intervals[-1].active_to
        if last_finish is None:
            raise ValueError("Data error - the last interval should NOT be unbounded. This is an error with inputs.")

        resulting_active_intervals.append(
            _new_active_interval(
                active_from=last_finish,
                active_to=None,
                controls=active_controls_by_id,
                defaults=active_defaults_by_id,
            )
        )

    return resulting_active_intervals


async def calculate_schedule_values(session: AsyncSession, now: datetime) -> list[ScheduledControlValues]:
    """Fetches the schedule of controls from now until the end of time. This will consider all current/upcoming
    active controls and defaults and their associated primacy rules.

    The resulting schedule will be the unambiguous sequence of control values, starting from now and finishing with
    an infinite length schedule. There will be NO overlaps/gaps in the sequential scheduled values (ordered by
    active_from)"""

    # Consult the DB to get the raw data - these will all be sorted in ascending "start" times
    defaults = await fetch_defaults_from(session, now)
    controls = await fetch_controls_active_from(session, now)
