from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from cactus_juice.api.deps import get_session
from cactus_juice.crud import (
    fetch_dynamic_prices_active_from,
    fetch_ocpp_metadata,
    fetch_ocpp_readings_in_range,
    fetch_satec_configs,
    fetch_satec_readings_in_range,
    fetch_task_health,
)
from cactus_juice.csipaus.controls import calculate_schedule_values
from cactus_juice.csipaus.dto import ScheduledControlValues
from cactus_juice.model import CSIPAusDynamicPrice, OCPPMetadata, OCPPReading, SatecReading, TaskHealth
from cactus_juice.tasks import TASKS

router = APIRouter(prefix="/api/telemetry", tags=["telemetry"])

# The chart window: 5 minutes wide, starting 60 seconds in the past (ie mostly the near future -
# schedule/price data looks forward from now, readings looks backward).
WINDOW_LOOKBACK_SECONDS = 60
WINDOW_DURATION_SECONDS = 300


class ScheduledValuesResponse(BaseModel):
    """Mirrors cactus_juice.csipaus.dto.ScheduledControlValues - a flattened window of what DERControl/Default
    values are active for [active_from, active_to)."""

    active_from: datetime
    active_to: datetime | None
    connect: bool | None
    energize: bool | None
    import_limit_watts: int | None
    export_limit_watts: int | None
    load_limit_watts: int | None
    generation_limit_watts: int | None
    storage_target_watts: int | None
    ramp_percent_max_second_hundredths: int | None
    ramp_time_seconds: int | None

    @staticmethod
    def from_model(scv: ScheduledControlValues) -> "ScheduledValuesResponse":
        return ScheduledValuesResponse(
            active_from=scv.active_from,
            active_to=scv.active_to,
            connect=scv.values.connect,
            energize=scv.values.energize,
            import_limit_watts=scv.values.import_limit_watts,
            export_limit_watts=scv.values.export_limit_watts,
            load_limit_watts=scv.values.load_limit_watts,
            generation_limit_watts=scv.values.generation_limit_watts,
            storage_target_watts=scv.values.storage_target_watts,
            ramp_percent_max_second_hundredths=scv.values.ramp_percent_max_second_hundredths,
            ramp_time_seconds=scv.values.ramp_time_seconds,
        )


class DynamicPriceResponse(BaseModel):
    mrid: str
    primacy: int  # Lower value takes precedence over higher when prices overlap - mirrors CSIPAusControl.primacy
    started_at: datetime
    finished_at: datetime
    cancelled_at: datetime | None
    price_kwh: Decimal | None

    @staticmethod
    def from_model(p: CSIPAusDynamicPrice) -> "DynamicPriceResponse":
        return DynamicPriceResponse(
            mrid=p.mrid,
            primacy=p.primacy,
            started_at=p.started_at,
            finished_at=p.finished_at,
            cancelled_at=p.cancelled_at,
            price_kwh=p.price_kwh,
        )


class OCPPReadingResponse(BaseModel):
    reading_start: datetime
    frequency_hz: float | None
    import_active_power_watts: float | None
    export_active_power_watts: float | None
    import_reactive_power_var: float | None
    export_reactive_power_var: float | None
    soc_percent: float | None
    voltage_volts: float | None

    @staticmethod
    def from_model(r: OCPPReading) -> "OCPPReadingResponse":
        return OCPPReadingResponse(
            reading_start=r.reading_start,
            frequency_hz=r.frequency_hz,
            import_active_power_watts=r.import_active_power_watts,
            export_active_power_watts=r.export_active_power_watts,
            import_reactive_power_var=r.import_reactive_power_var,
            export_reactive_power_var=r.export_reactive_power_var,
            soc_percent=r.soc_percent,
            voltage_volts=r.voltage_volts,
        )


class OCPPMetadataResponse(BaseModel):
    created_at: datetime
    max_voltage_volts: float | None
    min_voltage_volts: float | None
    max_power_watts: float | None
    max_charge_rate_watts: float | None
    max_discharge_rate_watts: float | None
    set_grad_w: float | None

    @staticmethod
    def from_model(m: OCPPMetadata) -> "OCPPMetadataResponse":
        return OCPPMetadataResponse(
            created_at=m.created_at,
            max_voltage_volts=m.max_voltage_volts,
            min_voltage_volts=m.min_voltage_volts,
            max_power_watts=m.max_power_watts,
            max_charge_rate_watts=m.max_charge_rate_watts,
            max_discharge_rate_watts=m.max_discharge_rate_watts,
            set_grad_w=m.set_grad_w,
        )


class SatecReadingResponse(BaseModel):
    """A subset of a SatecReading's totals block - just what the telemetry chart needs. label identifies which
    SatecConfig (meter) this reading came from, so readings from different meters can be told apart/charted as
    separate series."""

    label: str
    reading_start: datetime
    total_kw: float
    total_kvar: float
    v_avg_ln: float
    frequency: float

    @staticmethod
    def from_model(r: SatecReading, label: str) -> "SatecReadingResponse":
        return SatecReadingResponse(
            label=label,
            reading_start=r.reading_start,
            total_kw=r.total_kw,
            total_kvar=r.total_kvar,
            v_avg_ln=r.v_avg_ln,
            frequency=r.frequency,
        )


class TaskHealthResponse(BaseModel):
    """last_run_at is None when task_name is a registered task (see cactus_juice.tasks.TASKS) that has never
    reported a TaskHealth row - eg it hasn't run yet, or the deployment doesn't run it at all."""

    task_name: str
    last_run_at: datetime | None
    last_exception_at: datetime | None
    last_exception: str | None

    @staticmethod
    def from_model(task_name: str, h: TaskHealth | None) -> "TaskHealthResponse":
        if h is None:
            return TaskHealthResponse(
                task_name=task_name, last_run_at=None, last_exception_at=None, last_exception=None
            )
        return TaskHealthResponse(
            task_name=h.task_name,
            last_run_at=h.last_run_at,
            last_exception_at=h.last_exception_at,
            last_exception=h.last_exception,
        )


class TelemetrySnapshotResponse(BaseModel):
    now: datetime
    window_start: datetime
    window_end: datetime
    schedule: list[ScheduledValuesResponse]
    dynamic_prices: list[DynamicPriceResponse]
    ocpp_readings: list[OCPPReadingResponse]
    ocpp_metadata: OCPPMetadataResponse | None
    satec_readings: list[SatecReadingResponse]
    task_health: list[TaskHealthResponse]


@router.get("/snapshot", response_model=TelemetrySnapshotResponse)
async def get_telemetry_snapshot(session: AsyncSession = Depends(get_session)) -> TelemetrySnapshotResponse:
    """Fetches a single point-in-time snapshot of everything the telemetry chart needs to render: the resolved
    DERControl/Default schedule, active dynamic prices and OCPP/Satec readings/metadata across a window running
    from WINDOW_LOOKBACK_SECONDS in the past through to WINDOW_DURATION_SECONDS after that.

    The schedule is resolved from window_start (not now) so the chart shows a glimpse of what was actually in
    effect over the last WINDOW_LOOKBACK_SECONDS, not just what's upcoming."""
    now = datetime.now(UTC)
    window_start = now - timedelta(seconds=WINDOW_LOOKBACK_SECONDS)
    window_end = window_start + timedelta(seconds=WINDOW_DURATION_SECONDS)

    schedule = await calculate_schedule_values(session, window_start)
    dynamic_prices = await fetch_dynamic_prices_active_from(session, window_start)
    ocpp_readings = await fetch_ocpp_readings_in_range(session, window_start, window_end)
    ocpp_metadata = await fetch_ocpp_metadata(session)
    satec_configs = await fetch_satec_configs(session)
    satec_readings = await fetch_satec_readings_in_range(session, window_start, window_end)

    # One entry per registered task (see cactus_juice.tasks.TASKS) - a task with no TaskHealth row yet (never
    # run) still gets an entry, just with last_run_at=None, so the frontend can tell "missing" apart from
    # "stale" rather than only seeing whatever happens to already be in the table.
    task_health_by_name = {h.task_name: h for h in await fetch_task_health(session)}

    # Readings only carry the FK id, not the label - resolve it here so the frontend can group/label series
    # without needing to know about SatecConfig at all. Falls back to a synthetic label if the parent config
    # was deleted after the reading was recorded.
    satec_labels_by_id = {c.satec_config_id: c.label for c in satec_configs}

    return TelemetrySnapshotResponse(
        now=now,
        window_start=window_start,
        window_end=window_end,
        schedule=[ScheduledValuesResponse.from_model(s) for s in schedule],
        dynamic_prices=[DynamicPriceResponse.from_model(p) for p in dynamic_prices],
        ocpp_readings=[OCPPReadingResponse.from_model(r) for r in ocpp_readings],
        ocpp_metadata=OCPPMetadataResponse.from_model(ocpp_metadata) if ocpp_metadata is not None else None,
        satec_readings=[
            SatecReadingResponse.from_model(r, satec_labels_by_id.get(r.satec_config_id, f"Meter {r.satec_config_id}"))
            for r in satec_readings
        ],
        task_health=[
            TaskHealthResponse.from_model(name, task_health_by_name.get(name)) for name in sorted(TASKS)
        ],
    )
