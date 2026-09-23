import { apiGet } from './http'

/** Mirrors cactus_juice.api.routers.telemetry.ScheduledValuesResponse - a flattened window of what
 * DERControl/Default values are active for [activeFrom, activeTo). */
export interface ScheduledValues {
  activeFrom: string
  activeTo: string | null
  connect: boolean | null
  energize: boolean | null
  importLimitWatts: number | null
  exportLimitWatts: number | null
  loadLimitWatts: number | null
  generationLimitWatts: number | null
  storageTargetWatts: number | null
  rampPercentMaxSecondHundredths: number | null
  rampTimeSeconds: number | null
}

/** Mirrors cactus_juice.api.routers.telemetry.DynamicPriceResponse. */
export interface DynamicPrice {
  mrid: string
  primacy: number
  startedAt: string
  finishedAt: string
  cancelledAt: string | null
  priceKwh: number | null
}

/** Mirrors cactus_juice.api.routers.telemetry.OCPPReadingResponse. */
export interface OcppReading {
  readingStart: string
  frequencyHz: number | null
  importActivePowerWatts: number | null
  exportActivePowerWatts: number | null
  importReactivePowerVar: number | null
  exportReactivePowerVar: number | null
  socPercent: number | null
  voltageVolts: number | null
}

/** Mirrors cactus_juice.api.routers.telemetry.OCPPMetadataResponse. */
export interface OcppMetadata {
  createdAt: string
  maxVoltageVolts: number | null
  minVoltageVolts: number | null
  maxPowerWatts: number | null
  maxChargeRateWatts: number | null
  maxDischargeRateWatts: number | null
  setGradW: number | null
}

/** Mirrors cactus_juice.api.routers.telemetry.SatecReadingResponse - label identifies which SatecConfig
 * (meter) this reading came from, so readings from different meters can be told apart/charted as separate
 * series. */
export interface SatecReading {
  label: string
  readingStart: string
  totalKw: number
  totalKvar: number
  vAvgLn: number
  frequency: number
}

/** Mirrors cactus_juice.api.routers.telemetry.TaskHealthResponse. lastRunAt is null when the task is registered
 * (see cactus_juice.tasks.TASKS) but has never reported in. */
export interface TaskHealth {
  taskName: string
  lastRunAt: string | null
  lastExceptionAt: string | null
  lastException: string | null
}

/** Mirrors cactus_juice.api.routers.telemetry.TelemetrySnapshotResponse. */
export interface TelemetrySnapshot {
  now: string
  windowStart: string
  windowEnd: string
  schedule: ScheduledValues[]
  dynamicPrices: DynamicPrice[]
  ocppReadings: OcppReading[]
  ocppMetadata: OcppMetadata | null
  satecReadings: SatecReading[]
  taskHealth: TaskHealth[]
}

/** Raw shapes returned by the backend (snake_case, matches the FastAPI response models). price_kwh is a Decimal
 * so pydantic serializes it as a string - it's parsed back to a number in fromWire. */
interface ScheduledValuesWire {
  active_from: string
  active_to: string | null
  connect: boolean | null
  energize: boolean | null
  import_limit_watts: number | null
  export_limit_watts: number | null
  load_limit_watts: number | null
  generation_limit_watts: number | null
  storage_target_watts: number | null
  ramp_percent_max_second_hundredths: number | null
  ramp_time_seconds: number | null
}

interface DynamicPriceWire {
  mrid: string
  primacy: number
  started_at: string
  finished_at: string
  cancelled_at: string | null
  price_kwh: string | null
}

interface OcppReadingWire {
  reading_start: string
  frequency_hz: number | null
  import_active_power_watts: number | null
  export_active_power_watts: number | null
  import_reactive_power_var: number | null
  export_reactive_power_var: number | null
  soc_percent: number | null
  voltage_volts: number | null
}

interface OcppMetadataWire {
  created_at: string
  max_voltage_volts: number | null
  min_voltage_volts: number | null
  max_power_watts: number | null
  max_charge_rate_watts: number | null
  max_discharge_rate_watts: number | null
  set_grad_w: number | null
}

interface SatecReadingWire {
  label: string
  reading_start: string
  total_kw: number
  total_kvar: number
  v_avg_ln: number
  frequency: number
}

interface TaskHealthWire {
  task_name: string
  last_run_at: string | null
  last_exception_at: string | null
  last_exception: string | null
}

interface TelemetrySnapshotWire {
  now: string
  window_start: string
  window_end: string
  schedule: ScheduledValuesWire[]
  dynamic_prices: DynamicPriceWire[]
  ocpp_readings: OcppReadingWire[]
  ocpp_metadata: OcppMetadataWire | null
  satec_readings: SatecReadingWire[]
  task_health: TaskHealthWire[]
}

function scheduledValuesFromWire(wire: ScheduledValuesWire): ScheduledValues {
  return {
    activeFrom: wire.active_from,
    activeTo: wire.active_to,
    connect: wire.connect,
    energize: wire.energize,
    importLimitWatts: wire.import_limit_watts,
    exportLimitWatts: wire.export_limit_watts,
    loadLimitWatts: wire.load_limit_watts,
    generationLimitWatts: wire.generation_limit_watts,
    storageTargetWatts: wire.storage_target_watts,
    rampPercentMaxSecondHundredths: wire.ramp_percent_max_second_hundredths,
    rampTimeSeconds: wire.ramp_time_seconds,
  }
}

function dynamicPriceFromWire(wire: DynamicPriceWire): DynamicPrice {
  return {
    mrid: wire.mrid,
    primacy: wire.primacy,
    startedAt: wire.started_at,
    finishedAt: wire.finished_at,
    cancelledAt: wire.cancelled_at,
    priceKwh: wire.price_kwh === null ? null : Number(wire.price_kwh),
  }
}

function ocppReadingFromWire(wire: OcppReadingWire): OcppReading {
  return {
    readingStart: wire.reading_start,
    frequencyHz: wire.frequency_hz,
    importActivePowerWatts: wire.import_active_power_watts,
    exportActivePowerWatts: wire.export_active_power_watts,
    importReactivePowerVar: wire.import_reactive_power_var,
    exportReactivePowerVar: wire.export_reactive_power_var,
    socPercent: wire.soc_percent,
    voltageVolts: wire.voltage_volts,
  }
}

function ocppMetadataFromWire(wire: OcppMetadataWire): OcppMetadata {
  return {
    createdAt: wire.created_at,
    maxVoltageVolts: wire.max_voltage_volts,
    minVoltageVolts: wire.min_voltage_volts,
    maxPowerWatts: wire.max_power_watts,
    maxChargeRateWatts: wire.max_charge_rate_watts,
    maxDischargeRateWatts: wire.max_discharge_rate_watts,
    setGradW: wire.set_grad_w,
  }
}

function satecReadingFromWire(wire: SatecReadingWire): SatecReading {
  return {
    label: wire.label,
    readingStart: wire.reading_start,
    totalKw: wire.total_kw,
    totalKvar: wire.total_kvar,
    vAvgLn: wire.v_avg_ln,
    frequency: wire.frequency,
  }
}

function taskHealthFromWire(wire: TaskHealthWire): TaskHealth {
  return {
    taskName: wire.task_name,
    lastRunAt: wire.last_run_at,
    lastExceptionAt: wire.last_exception_at,
    lastException: wire.last_exception,
  }
}

function fromWire(wire: TelemetrySnapshotWire): TelemetrySnapshot {
  return {
    now: wire.now,
    windowStart: wire.window_start,
    windowEnd: wire.window_end,
    schedule: wire.schedule.map(scheduledValuesFromWire),
    dynamicPrices: wire.dynamic_prices.map(dynamicPriceFromWire),
    ocppReadings: wire.ocpp_readings.map(ocppReadingFromWire),
    ocppMetadata: wire.ocpp_metadata ? ocppMetadataFromWire(wire.ocpp_metadata) : null,
    satecReadings: wire.satec_readings.map(satecReadingFromWire),
    taskHealth: wire.task_health.map(taskHealthFromWire),
  }
}

export async function fetchTelemetrySnapshot(): Promise<TelemetrySnapshot> {
  return fromWire(await apiGet<TelemetrySnapshotWire>('/api/telemetry/snapshot'))
}
