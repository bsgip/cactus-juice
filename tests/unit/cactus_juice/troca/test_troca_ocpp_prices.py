from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from assertical.fake.generator import generate_class_instance

from cactus_juice.model import CSIPAusDynamicPrice
from cactus_juice.troca import ocpp_prices
from cactus_juice.troca.mapping import PriceSegment
from cactus_juice.troca.models import OcppChargingProfile, OcppTarget
from cactus_juice.troca.ocpp_prices import (
    PRICE_PROFILE_EVSE_ID,
    PRICE_PROFILE_ID,
    PRICE_PROFILE_PURPOSE,
    REFRESH_INTERVAL,
    PushedPrices,
    requires_push,
    sync_ocpp_prices,
)

NOW = datetime(2026, 9, 28, 4, 0, 0, tzinfo=UTC)
TARGET = OcppTarget("qocppConnector2.1", "2.1", "FR*TRI*E123")
SEGMENTS = [PriceSegment(NOW, NOW + timedelta(minutes=30), Decimal("0.30"))]
OTHER_SEGMENTS = [PriceSegment(NOW, NOW + timedelta(minutes=30), Decimal("0.31"))]


@pytest.mark.parametrize(
    "last, desired, expected",
    [
        (None, SEGMENTS, True),
        (None, [], True),  # Clears any stale profile at startup
        (PushedPrices(SEGMENTS, NOW), SEGMENTS, False),
        (PushedPrices(SEGMENTS, NOW), OTHER_SEGMENTS, True),
        (PushedPrices(SEGMENTS, NOW), [], True),
        (PushedPrices([], NOW), [], False),
        (PushedPrices(SEGMENTS, NOW - REFRESH_INTERVAL), SEGMENTS, True),  # Periodic refresh
    ],
)
def test_requires_push(last: PushedPrices | None, desired: list[PriceSegment], expected: bool):
    assert requires_push(last, desired, NOW) is expected


@dataclass
class FakeTrocaClient:
    set_status: str = "Accepted"
    set_calls: list[tuple[int, OcppChargingProfile]] = field(default_factory=list)
    clear_calls: list[int] = field(default_factory=list)

    async def set_charging_profile(self, target: OcppTarget, evse_id: int, p: OcppChargingProfile) -> str:
        self.set_calls.append((evse_id, p))
        return self.set_status

    async def clear_charging_profile(self, target: OcppTarget, charging_profile_id: int) -> str:
        self.clear_calls.append(charging_profile_id)
        return "Accepted"


def dynamic_price(price_kwh: str) -> CSIPAusDynamicPrice:
    return generate_class_instance(
        CSIPAusDynamicPrice,
        started_at=NOW,
        finished_at=NOW + timedelta(minutes=30),
        cancelled_at=None,
        price_kwh=Decimal(price_kwh),
    )


async def test_sync_ocpp_prices(monkeypatch: pytest.MonkeyPatch):
    prices: list[CSIPAusDynamicPrice] = [dynamic_price("0.30")]

    async def fetch_dynamic_prices_active_from(session, epoch):
        assert epoch == NOW
        return prices

    monkeypatch.setattr(ocpp_prices, "fetch_dynamic_prices_active_from", fetch_dynamic_prices_active_from)
    client = FakeTrocaClient(set_status="Rejected")  # Station status doesn't matter - only API errors

    last = await sync_ocpp_prices(client, TARGET, None, NOW, None)  # ty: ignore[invalid-argument-type]
    assert last == PushedPrices(SEGMENTS, NOW)
    assert len(client.set_calls) == 1
    evse_id, profile = client.set_calls[0]
    assert evse_id == PRICE_PROFILE_EVSE_ID
    assert profile.id == PRICE_PROFILE_ID and profile.charging_profile_purpose == PRICE_PROFILE_PURPOSE
    schedule = profile.charging_schedule[0].absolute_price_schedule
    assert schedule is not None and schedule.price_schedule_id == int(NOW.timestamp())

    # Unchanged - no push
    assert await sync_ocpp_prices(client, TARGET, None, NOW, last) is last  # ty: ignore[invalid-argument-type]
    assert len(client.set_calls) == 1

    # No prices - the profile is cleared
    prices.clear()
    last = await sync_ocpp_prices(client, TARGET, None, NOW, last)  # ty: ignore[invalid-argument-type]
    assert last == PushedPrices([], NOW)
    assert client.clear_calls == [PRICE_PROFILE_ID]


async def test_sync_ocpp_prices_api_error(monkeypatch: pytest.MonkeyPatch):
    """An API error propagates (so is retried next poll) rather than being recorded as pushed"""

    async def fetch_dynamic_prices_active_from(session, epoch):
        return [dynamic_price("0.30")]

    async def set_charging_profile(target, evse_id, p):
        raise ConnectionError("boom")

    monkeypatch.setattr(ocpp_prices, "fetch_dynamic_prices_active_from", fetch_dynamic_prices_active_from)
    client = FakeTrocaClient()
    client.set_charging_profile = set_charging_profile  # ty: ignore[invalid-assignment]
    with pytest.raises(ConnectionError):
        await sync_ocpp_prices(client, TARGET, None, NOW, None)  # ty: ignore[invalid-argument-type]
