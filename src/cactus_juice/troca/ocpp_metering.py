"""Configures which meter values the charging station samples (via Troca's OCPP passthrough), so that the readings
Troca collects (see poll_readings) carry more than just the energy register.

Out of the box the Trialog simulator only samples Energy.Active.Import.Register every 300s. This sets both
SampledDataCtrlr (readings during a transaction) and AlignedDataCtrlr (clock aligned readings, with or without a
transaction) to METERING_MEASURANDS every METERING_INTERVAL_SECONDS."""

import logging
from collections.abc import Sequence

from cactus_juice.troca.client import TrocaClient
from cactus_juice.troca.models import OcppComponent, OcppTarget, OcppVariable, SetVariableData

logger = logging.getLogger(__name__)

# The measurands we'd like sampled, in priority order. Stations only support a subset (the Trialog simulator rejects
# Voltage, every *.Export measurand and Power.Active.Setpoint) - see configure_metering for how that's handled.
METERING_MEASURANDS = (
    "Power.Active.Import",
    "Power.Active.Export",
    "Voltage",
    "SoC",
    "Energy.Active.Import.Register",
    "Energy.Active.Export.Register",
    "Current.Import",
    "Current.Export",
    "Power.Active.Setpoint",
)

METERING_INTERVAL_SECONDS = 10

# (component, variable) pairs for the measurand lists / sample intervals of each meter value source
MEASURAND_VARIABLES = (("SampledDataCtrlr", "TxUpdatedMeasurands"), ("AlignedDataCtrlr", "Measurands"))
INTERVAL_VARIABLES = (("SampledDataCtrlr", "TxUpdatedInterval"), ("AlignedDataCtrlr", "Interval"))


def _set_variable(component: str, variable: str, value: str) -> SetVariableData:
    return SetVariableData(
        component=OcppComponent(name=component), variable=OcppVariable(name=variable), attribute_value=value
    )


async def _try_set(client: TrocaClient, target: OcppTarget, component: str, variable: str, value: str) -> bool:
    results = await client.set_variables(target, [_set_variable(component, variable, value)])
    return any(r.accepted for r in results)


async def configure_measurands(
    client: TrocaClient, target: OcppTarget, component: str, variable: str, measurands: Sequence[str]
) -> list[str]:
    """Sets the measurand list component.variable to as much of measurands as the station supports - returning
    the measurands that were set (empty if the station accepted none of them, leaving its existing value as is).

    A measurand list is rejected as a whole if any one measurand is unsupported (and the valid values can't be
    discovered via the passthrough) - so if the full list is rejected, the list is built up one measurand at a
    time, keeping each one the station accepts. A rejected SetVariables leaves the station's value unchanged."""

    if await _try_set(client, target, component, variable, ",".join(measurands)):
        return list(measurands)

    supported: list[str] = []
    for m in measurands:
        if await _try_set(client, target, component, variable, ",".join([*supported, m])):
            supported.append(m)

    rejected = [m for m in measurands if m not in supported]
    logger.warning(f"Station {target.station_name} rejected {component}.{variable} measurands {rejected}.")
    return supported


async def configure_metering(client: TrocaClient, target: OcppTarget) -> None:
    """Configures the station's sampled and aligned meter values (see module docs). Unsupported measurands /
    rejected intervals are logged, but otherwise ignored."""

    results = await client.set_variables(
        target, [_set_variable(c, v, str(METERING_INTERVAL_SECONDS)) for c, v in INTERVAL_VARIABLES]
    )
    for r in results:
        if not r.accepted:
            logger.warning(
                f"Station {target.station_name} rejected {r.component.name}.{r.variable.name} ="
                f" {METERING_INTERVAL_SECONDS} with status '{r.attribute_status}'."
            )

    for component, variable in MEASURAND_VARIABLES:
        measurands = await configure_measurands(client, target, component, variable, METERING_MEASURANDS)
        logger.info(f"Station {target.station_name} {component}.{variable} set to {measurands}.")
