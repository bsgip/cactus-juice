import { apiDelete, apiGet, apiSendJson } from './http'

export type SatecModel = 'em133' | 'em235'
export type SatecParity = 'N' | 'E' | 'O'

/** Mirrors cactus_juice.api.routers.satec.SatecConfigResponse. Unlike CSIPAusConfig/TrocaConfig, many of these
 * can exist at once - one per meter being polled. */
export interface SatecConfig {
  id: number
  createdAt: string
  label: string
  pollRateSeconds: number
  model: SatecModel
  host: string | null
  port: string | null
  portTcp: number
  unit: number
  baud: number
  parity: SatecParity
  timeoutSeconds: number
  includePhases: boolean
  includeEnergy: boolean
  floatMode: boolean
}

interface SatecConfigWire {
  id: number
  created_at: string
  label: string
  poll_rate_seconds: number
  model: SatecModel
  host: string | null
  port: string | null
  port_tcp: number
  unit: number
  baud: number
  parity: SatecParity
  timeout_seconds: number
  include_phases: boolean
  include_energy: boolean
  float_mode: boolean
}

function fromWire(wire: SatecConfigWire): SatecConfig {
  return {
    id: wire.id,
    createdAt: wire.created_at,
    label: wire.label,
    pollRateSeconds: wire.poll_rate_seconds,
    model: wire.model,
    host: wire.host,
    port: wire.port,
    portTcp: wire.port_tcp,
    unit: wire.unit,
    baud: wire.baud,
    parity: wire.parity,
    timeoutSeconds: wire.timeout_seconds,
    includePhases: wire.include_phases,
    includeEnergy: wire.include_energy,
    floatMode: wire.float_mode,
  }
}

export async function fetchSatecConfigs(): Promise<SatecConfig[]> {
  const wire = await apiGet<SatecConfigWire[]>('/api/satec-config')
  return wire.map(fromWire)
}

/** Values submitted from a SatecConfig form - used for both creating and updating an entry. */
export interface SatecConfigInput {
  label: string
  pollRateSeconds: number
  model: SatecModel
  host: string | null
  port: string | null
  portTcp: number
  unit: number
  baud: number
  parity: SatecParity
  timeoutSeconds: number
  includePhases: boolean
  includeEnergy: boolean
  floatMode: boolean
}

function toWire(values: SatecConfigInput) {
  return {
    label: values.label,
    poll_rate_seconds: values.pollRateSeconds,
    model: values.model,
    host: values.host,
    port: values.port,
    port_tcp: values.portTcp,
    unit: values.unit,
    baud: values.baud,
    parity: values.parity,
    timeout_seconds: values.timeoutSeconds,
    include_phases: values.includePhases,
    include_energy: values.includeEnergy,
    float_mode: values.floatMode,
  }
}

export async function createSatecConfig(values: SatecConfigInput): Promise<SatecConfig> {
  return fromWire(await apiSendJson<SatecConfigWire>('/api/satec-config', 'POST', toWire(values)))
}

export async function updateSatecConfig(id: number, values: SatecConfigInput): Promise<SatecConfig> {
  return fromWire(await apiSendJson<SatecConfigWire>(`/api/satec-config/${id}`, 'PUT', toWire(values)))
}

export async function deleteSatecConfig(id: number): Promise<void> {
  await apiDelete(`/api/satec-config/${id}`)
}
