from dataclasses import dataclass, field

import pytest

from cactus_juice.troca.models import OcppTarget, SetVariableData, SetVariableResult
from cactus_juice.troca.ocpp_metering import (
    METERING_INTERVAL_SECONDS,
    METERING_MEASURANDS,
    configure_measurands,
    configure_metering,
)

TARGET = OcppTarget("qocppConnector2.1", "2.1", "FR*TRI*E123")

# What the Trialog simulator was observed to accept (2026-10-13)
SIMULATOR_MEASURANDS = {"Energy.Active.Import.Register", "Power.Active.Import", "Current.Import", "SoC"}


@dataclass
class FakeStation:
    """Accepts a measurand list only if every measurand is supported - mirroring a real station."""

    supported_measurands: set[str]
    reject_intervals: bool = False
    values: dict[str, str] = field(default_factory=dict)  # "Component.Variable" -> last accepted value
    requests: list[list[SetVariableData]] = field(default_factory=list)

    async def set_variables(self, target: OcppTarget, requests: list[SetVariableData]) -> list[SetVariableResult]:
        self.requests.append(requests)
        results = []
        for r in requests:
            key = f"{r.component.name}.{r.variable.name}"
            if "Measurands" in r.variable.name:
                accepted = set(r.attribute_value.split(",")) <= self.supported_measurands
            else:
                accepted = not self.reject_intervals
            if accepted:
                self.values[key] = r.attribute_value
            results.append(
                SetVariableResult(
                    attribute_status="Accepted" if accepted else "Rejected", component=r.component, variable=r.variable
                )
            )
        return results


@pytest.mark.parametrize(
    "supported, expected, expected_calls",
    [
        (set(METERING_MEASURANDS), list(METERING_MEASURANDS), 1),  # Full list accepted first time
        (
            SIMULATOR_MEASURANDS,
            ["Power.Active.Import", "SoC", "Energy.Active.Import.Register", "Current.Import"],
            1 + len(METERING_MEASURANDS),
        ),
        (set(), [], 1 + len(METERING_MEASURANDS)),
    ],
)
async def test_configure_measurands(supported: set[str], expected: list[str], expected_calls: int):
    station = FakeStation(supported)
    actual = await configure_measurands(
        station,  # ty: ignore[invalid-argument-type]
        TARGET,
        "SampledDataCtrlr",
        "TxUpdatedMeasurands",
        METERING_MEASURANDS,
    )
    assert actual == expected
    assert len(station.requests) == expected_calls
    assert station.values.get("SampledDataCtrlr.TxUpdatedMeasurands") == (",".join(expected) if expected else None)


@pytest.mark.parametrize("reject_intervals", [False, True])
async def test_configure_metering(reject_intervals: bool):
    station = FakeStation(SIMULATOR_MEASURANDS, reject_intervals=reject_intervals)
    await configure_metering(station, TARGET)  # ty: ignore[invalid-argument-type]

    expected_measurands = "Power.Active.Import,SoC,Energy.Active.Import.Register,Current.Import"
    expected = {
        "SampledDataCtrlr.TxUpdatedMeasurands": expected_measurands,
        "AlignedDataCtrlr.Measurands": expected_measurands,
    }
    if not reject_intervals:
        expected["SampledDataCtrlr.TxUpdatedInterval"] = str(METERING_INTERVAL_SECONDS)
        expected["AlignedDataCtrlr.Interval"] = str(METERING_INTERVAL_SECONDS)
    assert station.values == expected
