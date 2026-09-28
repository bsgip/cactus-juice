import { apiGet, apiSendJson } from './http'

/** Mirrors cactus_juice.troca.models.ScheduleSyncMode */
export type ScheduleSyncMode = 'ocpp' | 'troca_session'

/** Mirrors cactus_juice.api.routers.troca.TrocaConfigResponse */
export interface TrocaConfig {
  createdAt: string | null
  baseUrl: string | null
  basicUser: string | null
  hasBasicPassword: boolean
  connectorId: string | null
  readingPollRateSeconds: number | null
  rampStepSeconds: number | null
  schedulePollRateSeconds: number | null
  metadataPollRateSeconds: number | null
  scheduleSyncMode: ScheduleSyncMode | null
  // Values that can be discovered via the Troca API - null means "discover on every poll"
  ocppConnectorName: string | null
  ocppVersion: string | null
  ocppStationName: string | null
  ocppEvseNb: number | null
  evseId: string | null
}

/** Raw shape returned by the backend (snake_case, matches the FastAPI response model). */
interface TrocaConfigWire {
  created_at: string | null
  base_url: string | null
  basic_user: string | null
  has_basic_password: boolean
  connector_id: string | null
  reading_poll_rate_seconds: number | null
  ramp_step_seconds: number | null
  schedule_poll_rate_seconds: number | null
  metadata_poll_rate_seconds: number | null
  schedule_sync_mode: ScheduleSyncMode | null
  ocpp_connector_name: string | null
  ocpp_version: string | null
  ocpp_station_name: string | null
  ocpp_evse_nb: number | null
  evse_id: string | null
}

function fromWire(wire: TrocaConfigWire): TrocaConfig {
  return {
    createdAt: wire.created_at,
    baseUrl: wire.base_url,
    basicUser: wire.basic_user,
    hasBasicPassword: wire.has_basic_password,
    connectorId: wire.connector_id,
    readingPollRateSeconds: wire.reading_poll_rate_seconds,
    rampStepSeconds: wire.ramp_step_seconds,
    schedulePollRateSeconds: wire.schedule_poll_rate_seconds,
    metadataPollRateSeconds: wire.metadata_poll_rate_seconds,
    scheduleSyncMode: wire.schedule_sync_mode,
    ocppConnectorName: wire.ocpp_connector_name,
    ocppVersion: wire.ocpp_version,
    ocppStationName: wire.ocpp_station_name,
    ocppEvseNb: wire.ocpp_evse_nb,
    evseId: wire.evse_id,
  }
}

export async function fetchTrocaConfig(): Promise<TrocaConfig> {
  return fromWire(await apiGet<TrocaConfigWire>('/api/troca-config'))
}

/** Values submitted from the config form. Leave basicPassword blank to keep the currently stored password -
 * it's only mandatory the first time a TrocaConfig is created. */
export interface TrocaConfigUpdate {
  baseUrl: string
  basicUser: string
  basicPassword: string
  connectorId: string | null
  readingPollRateSeconds: number
  rampStepSeconds: number
  schedulePollRateSeconds: number
  metadataPollRateSeconds: number
  scheduleSyncMode: ScheduleSyncMode
  ocppConnectorName: string
  ocppVersion: string
  ocppStationName: string
  ocppEvseNb: number | null
  evseId: string | null
}

/** Blank discoverable values mean "discover on every poll" - sent as null. */
function blankToNull(value: string | null): string | null {
  const trimmed = value?.trim() ?? ''
  return trimmed ? trimmed : null
}

export async function updateTrocaConfig(values: TrocaConfigUpdate): Promise<TrocaConfig> {
  return fromWire(
    await apiSendJson<TrocaConfigWire>('/api/troca-config', 'PUT', {
      base_url: values.baseUrl.trim(),
      basic_user: values.basicUser.trim(),
      basic_password: values.basicPassword.trim() ? values.basicPassword : null,
      connector_id: values.connectorId,
      reading_poll_rate_seconds: values.readingPollRateSeconds,
      ramp_step_seconds: values.rampStepSeconds,
      schedule_poll_rate_seconds: values.schedulePollRateSeconds,
      metadata_poll_rate_seconds: values.metadataPollRateSeconds,
      schedule_sync_mode: values.scheduleSyncMode,
      ocpp_connector_name: blankToNull(values.ocppConnectorName),
      ocpp_version: blankToNull(values.ocppVersion),
      ocpp_station_name: blankToNull(values.ocppStationName),
      ocpp_evse_nb: values.ocppEvseNb,
      evse_id: blankToNull(values.evseId),
    }),
  )
}

/** Mirrors cactus_juice.api.routers.troca.TrocaConnectorResponse */
export interface TrocaConnector {
  name: string
  connectorId: string
  connectorType: string
  ocppVersion: string | null
  createdAt: string | null
  issuerId: string | null
  lastUpdated: string | null
  alternativeNames: string[]
}

interface TrocaConnectorWire {
  name: string
  connector_id: string
  connector_type: string
  ocpp_version: string | null
  created_at: string | null
  issuer_id: string | null
  last_updated: string | null
  alternative_names: string[]
}

function connectorFromWire(wire: TrocaConnectorWire): TrocaConnector {
  return {
    name: wire.name,
    connectorId: wire.connector_id,
    connectorType: wire.connector_type,
    ocppVersion: wire.ocpp_version,
    createdAt: wire.created_at,
    issuerId: wire.issuer_id,
    lastUpdated: wire.last_updated,
    alternativeNames: wire.alternative_names,
  }
}

/** Mirrors cactus_juice.api.routers.troca.TrocaStationResponse */
export interface TrocaStation {
  stationId: string
  name: string
  model: string | null
  vendorId: string | null
}

/** Mirrors cactus_juice.api.routers.troca.TrocaEvseResponse */
export interface TrocaEvse {
  evseId: string
  name: string | null
  stationName: string | null
  evseNb: number | null
}

/** Mirrors cactus_juice.api.routers.troca.TrocaDiscoveryResponse */
export interface TrocaDiscovery {
  connectors: TrocaConnector[]
  stations: TrocaStation[]
  evses: TrocaEvse[]
}

interface TrocaDiscoveryWire {
  connectors: TrocaConnectorWire[]
  stations: { station_id: string; name: string; model: string | null; vendor_id: string | null }[]
  evses: { evse_id: string; name: string | null; station_name: string | null; evse_nb: number | null }[]
}

/** Enumerates everything on the configured Troca API that can fill out a TrocaConfig - the backend errors if no
 * TrocaConfig is on record yet, so only call this once a config has been saved. */
export async function fetchTrocaDiscovery(): Promise<TrocaDiscovery> {
  const wire = await apiGet<TrocaDiscoveryWire>('/api/troca-config/discovery')
  return {
    connectors: wire.connectors.map(connectorFromWire),
    stations: wire.stations.map((s) => ({
      stationId: s.station_id,
      name: s.name,
      model: s.model,
      vendorId: s.vendor_id,
    })),
    evses: wire.evses.map((e) => ({
      evseId: e.evse_id,
      name: e.name,
      stationName: e.station_name,
      evseNb: e.evse_nb,
    })),
  }
}
