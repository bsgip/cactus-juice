import { apiGet, apiSendJson } from './http'

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
    }),
  )
}

/** Mirrors cactus_juice.api.routers.troca.TrocaConnectorResponse */
export interface TrocaConnector {
  name: string
  connectorId: string
  connectorType: string
  createdAt: string | null
  issuerId: string | null
  lastUpdated: string | null
  alternativeNames: string[]
}

interface TrocaConnectorWire {
  name: string
  connector_id: string
  connector_type: string
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
    createdAt: wire.created_at,
    issuerId: wire.issuer_id,
    lastUpdated: wire.last_updated,
    alternativeNames: wire.alternative_names,
  }
}

/** Enumerates connectors available on the configured Troca API - the backend errors if no TrocaConfig
 * is on record yet, so only call this once a config has been saved. */
export async function fetchTrocaConnectors(): Promise<TrocaConnector[]> {
  const wire = await apiGet<TrocaConnectorWire[]>('/api/troca-config/connectors')
  return wire.map(connectorFromWire)
}
