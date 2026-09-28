from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from assertical.fake.generator import generate_class_instance

from cactus_juice.csipaus.dto import ActiveValues, ScheduledControlValues
from cactus_juice.model import OCPPMetadata
from cactus_juice.troca import ocpp_schedule
from cactus_juice.troca.mapping import schedule_to_ocpp_profile
from cactus_juice.troca.models import OcppChargingProfile, OcppTarget
from cactus_juice.troca.ocpp_schedule import (
    FAILED_RETRY_INTERVAL,
    PROFILE_EVSE_ID,
    PROFILE_ID,
    PROFILE_PURPOSE,
    PROFILE_STACK_LEVEL,
    REFRESH_INTERVAL,
    PushedProfile,
    requires_push,
    sync_ocpp_schedule,
)

NOW = datetime(2026, 9, 28, 4, 0, 0, tzinfo=UTC)
TARGET = OcppTarget("qocppConnector2.1", "2.1", "FR*TRI*E123")
MAX_POWER = 40000.0


def raw_schedule(import_limit_watts: int | None) -> list[ScheduledControlValues]:
    v = generate_class_instance(ActiveValues, optional_is_none=True, import_limit_watts=import_limit_watts)
    return [ScheduledControlValues(active_from=NOW, active_to=None, values=v)]


def profile(import_limit_watts: int | None, now: datetime = NOW) -> OcppChargingProfile:
    p = schedule_to_ocpp_profile(
        raw_schedule(import_limit_watts), MAX_POWER, now, PROFILE_ID, PROFILE_STACK_LEVEL, PROFILE_PURPOSE
    )
    assert p is not None
    return p


@pytest.mark.parametrize(
    "last, desired, expected",
    [
        (None, profile(7000), True),  # Nothing pushed since startup
        (None, None, True),
        (PushedProfile(profile(7000), NOW, True), profile(7000), False),
        (PushedProfile(profile(7000), NOW, True), profile(6000), True),
        (PushedProfile(profile(7000), NOW, True), None, True),
        (PushedProfile(None, NOW, True), None, False),
        (PushedProfile(None, NOW, True), profile(7000), True),
        (PushedProfile(profile(7000), NOW - REFRESH_INTERVAL, True), profile(7000), True),  # Periodic refresh
        (PushedProfile(profile(7000), NOW, False), profile(7000), False),  # Rejected too recently to retry
        (PushedProfile(profile(7000), NOW - FAILED_RETRY_INTERVAL, False), profile(7000), True),
        (PushedProfile(profile(7000), NOW, False), profile(6000), True),  # Changed - so retry immediately
    ],
)
def test_requires_push(last: PushedProfile | None, desired: OcppChargingProfile | None, expected: bool):
    assert requires_push(last, desired, NOW) is expected


@dataclass
class FakeTrocaClient:
    """Duck-types the OCPP passthrough bits of TrocaClient"""

    set_status: str = "Accepted"
    clear_status: str = "Accepted"
    set_calls: list[tuple[OcppTarget, int, OcppChargingProfile]] = field(default_factory=list)
    clear_calls: list[tuple[OcppTarget, int]] = field(default_factory=list)

    async def set_charging_profile(self, target: OcppTarget, evse_id: int, p: OcppChargingProfile) -> str:
        self.set_calls.append((target, evse_id, p))
        return self.set_status

    async def clear_charging_profile(self, target: OcppTarget, charging_profile_id: int) -> str:
        self.clear_calls.append((target, charging_profile_id))
        return self.clear_status


@pytest.fixture
def patch_db(monkeypatch: pytest.MonkeyPatch):
    """Replaces the DB lookups made by sync_ocpp_schedule - returns a dict that can be updated to change them"""
    db: dict[str, Any] = {"metadata": OCPPMetadata(max_power_watts=MAX_POWER), "schedule": raw_schedule(7000)}

    async def fetch_ocpp_metadata(session):
        return db["metadata"]

    async def calculate_schedule_values(session, now):
        return db["schedule"]

    monkeypatch.setattr(ocpp_schedule, "fetch_ocpp_metadata", fetch_ocpp_metadata)
    monkeypatch.setattr(ocpp_schedule, "calculate_schedule_values", calculate_schedule_values)
    return db


async def test_sync_ocpp_schedule_pushes(patch_db):
    client = FakeTrocaClient()
    result = await sync_ocpp_schedule(client, TARGET, None, NOW, None)  # ty: ignore[invalid-argument-type]

    assert client.set_calls == [(TARGET, PROFILE_EVSE_ID, profile(7000))]
    assert not client.clear_calls
    assert result == PushedProfile(profile(7000), NOW, True)

    # Unchanged schedule is a no-op on the next poll
    later = NOW + timedelta(seconds=10)
    assert await sync_ocpp_schedule(client, TARGET, None, later, result) is result  # ty: ignore[invalid-argument-type]
    assert len(client.set_calls) == 1


async def test_sync_ocpp_schedule_rejected(patch_db):
    client = FakeTrocaClient(set_status="Rejected")
    result = await sync_ocpp_schedule(client, TARGET, None, NOW, None)  # ty: ignore[invalid-argument-type]
    assert result == PushedProfile(profile(7000), NOW, False)


@pytest.mark.parametrize("clear_status, accepted", [("Accepted", True), ("Unknown", True), ("Rejected", False)])
async def test_sync_ocpp_schedule_clears(patch_db, clear_status: str, accepted: bool):
    patch_db["schedule"] = []
    client = FakeTrocaClient(clear_status=clear_status)
    result = await sync_ocpp_schedule(client, TARGET, None, NOW, None)  # ty: ignore[invalid-argument-type]

    assert client.clear_calls == [(TARGET, PROFILE_ID)]
    assert not client.set_calls
    assert result == PushedProfile(None, NOW, accepted)


@pytest.mark.parametrize("metadata", [None, OCPPMetadata(max_power_watts=None)])
async def test_sync_ocpp_schedule_no_rated_power(patch_db, metadata: OCPPMetadata | None):
    patch_db["metadata"] = metadata
    client = FakeTrocaClient()
    last = PushedProfile(profile(1), NOW - timedelta(days=1), True)
    assert await sync_ocpp_schedule(client, TARGET, None, NOW, last) is last  # ty: ignore[invalid-argument-type]
    assert not client.set_calls and not client.clear_calls
