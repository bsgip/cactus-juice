from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from cactus_juice.troca.client import TrocaClient
from cactus_juice.troca.mapping import reading_to_db


@dataclass(slots=True)
class Pollable:
    last_poll: datetime
    poll_rate: timedelta


@dataclass(slots=True)
class ClientState:
    """Encapsulates all the state the client needs to know about the Troca server and its polling behaviour."""

    client: TrocaClient

    connector_id: str  # The ID of the Troca connector that will be used for comms
    readings_poll: Pollable  # Poll status of the readings
    schedule_poll: Pollable  # Poll status of the charge schedule
    ramp_step: timedelta

    def next_poll(self) -> datetime:
        """Calculates the next moment a poll/post should occur."""

        def _candidate_poll(p: Pollable | None) -> datetime | None:
            return None if p is None else p.last_poll + p.poll_rate

        candidate_times = [
            _candidate_poll(self.readings_poll),
            _candidate_poll(self.schedule_poll),
        ]

        # The default should never occur - but just in case
        return min((ct for ct in candidate_times if ct is not None), default=datetime.now(UTC))

    @staticmethod
    def new_instance(
        client: TrocaClient,
        connector_id: str,
        readings_poll_rate_seconds: int,
        schedule_poll_rate_seconds: int,
        ramp_step_seconds: int,
    ) -> "ClientState":
        return ClientState(
            client=client,
            connector_id=connector_id,
            readings_poll=Pollable(datetime.min, timedelta(seconds=readings_poll_rate_seconds)),
            schedule_poll=Pollable(datetime.min, timedelta(seconds=schedule_poll_rate_seconds)),
            ramp_step=timedelta(seconds=ramp_step_seconds),
        )


async def poll_readings(state: ClientState, session: AsyncSession, now: datetime) -> None:
    """Takes a snapshot of OCPP readings via troca and pushes them into the DB"""
    readings = await state.client.get_metering_data()
    session.add_all(reading_to_db(r) for r in readings)
    state.readings_poll.last_poll = now


# async def poll_schedules(state: ClientState, session: AsyncSession, now: datetime) -> None:


# async def run_polls(state: ClientState) -> datetime:
