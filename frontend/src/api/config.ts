import { apiGet, apiSendForm } from './http'

/** Mirrors cactus_juice.api.routers.config.CSIPAusConfigResponse */
export interface CSIPAusConfig {
  createdAt: string | null
  isAggregator: boolean
  nmi: string | null
  clientPen: number | null
  dcapUri: string | null
  verifyHostname: boolean
  verifySsl: boolean
  certificatePem: string | null
  sercaPem: string | null
  hasKeyPem: boolean
}

/** Raw shape returned by the backend (snake_case, matches the FastAPI response model). */
interface CSIPAusConfigWire {
  created_at: string | null
  is_aggregator: boolean
  nmi: string | null
  client_pen: number | null
  dcap_uri: string | null
  verify_hostname: boolean
  verify_ssl: boolean
  certificate_pem: string | null
  serca_pem: string | null
  has_key_pem: boolean
}

function fromWire(wire: CSIPAusConfigWire): CSIPAusConfig {
  return {
    createdAt: wire.created_at,
    isAggregator: wire.is_aggregator,
    nmi: wire.nmi,
    clientPen: wire.client_pen,
    dcapUri: wire.dcap_uri,
    verifyHostname: wire.verify_hostname,
    verifySsl: wire.verify_ssl,
    certificatePem: wire.certificate_pem,
    sercaPem: wire.serca_pem,
    hasKeyPem: wire.has_key_pem,
  }
}

export async function fetchCsipausConfig(): Promise<CSIPAusConfig> {
  return fromWire(await apiGet<CSIPAusConfigWire>('/api/config'))
}

/** Values submitted from the config form. Leave a *File field null to keep the currently stored PEM,
 * or set the matching clear* flag to remove it. */
export interface CSIPAusConfigUpdate {
  isAggregator: boolean
  nmi: string
  clientPen: string
  dcapUri: string
  verifyHostname: boolean
  verifySsl: boolean
  certificatePemFile: File | null
  keyPemFile: File | null
  sercaPemFile: File | null
  clearCertificatePem: boolean
  clearKeyPem: boolean
  clearSercaPem: boolean
}

export async function updateCsipausConfig(values: CSIPAusConfigUpdate): Promise<CSIPAusConfig> {
  const formData = new FormData()
  formData.append('is_aggregator', String(values.isAggregator))
  if (values.nmi.trim()) formData.append('nmi', values.nmi.trim())
  if (values.clientPen.trim()) formData.append('client_pen', values.clientPen.trim())
  if (values.dcapUri.trim()) formData.append('dcap_uri', values.dcapUri.trim())
  formData.append('verify_hostname', String(values.verifyHostname))
  formData.append('verify_ssl', String(values.verifySsl))
  formData.append('clear_certificate_pem', String(values.clearCertificatePem))
  formData.append('clear_key_pem', String(values.clearKeyPem))
  formData.append('clear_serca_pem', String(values.clearSercaPem))
  if (values.certificatePemFile) formData.append('certificate_pem', values.certificatePemFile)
  if (values.keyPemFile) formData.append('key_pem', values.keyPemFile)
  if (values.sercaPemFile) formData.append('serca_pem', values.sercaPemFile)

  return fromWire(await apiSendForm<CSIPAusConfigWire>('/api/config', 'PUT', formData))
}
