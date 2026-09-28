import {
  Alert,
  Autocomplete,
  Badge,
  Button,
  Divider,
  Fieldset,
  Group,
  LoadingOverlay,
  NumberInput,
  Paper,
  PasswordInput,
  SegmentedControl,
  Select,
  Stack,
  Text,
  TextInput,
} from '@mantine/core'
import { useForm } from '@mantine/form'
import { notifications } from '@mantine/notifications'
import { IconAlertCircle, IconDeviceFloppy, IconEraser, IconRefresh, IconWand } from '@tabler/icons-react'
import { useEffect } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { formatApiError } from '../api/http'
import {
  fetchTrocaConfig,
  fetchTrocaDiscovery,
  updateTrocaConfig,
  type ScheduleSyncMode,
  type TrocaConfig,
  type TrocaDiscovery,
} from '../api/troca'
import { PageHeading } from '../layout/AppLayout'

const CONFIG_QUERY_KEY = ['troca-config']
const DISCOVERY_QUERY_KEY = ['troca-discovery']

const SCHEDULE_SYNC_MODES: { value: ScheduleSyncMode; label: string; description: string }[] = [
  {
    value: 'ocpp',
    label: 'OCPP (direct)',
    description:
      'Sends a ChargingStationMaxProfile straight to the charging station via the OCPP passthrough. Expresses true import/export limits and caps whatever Troca itself schedules.',
  },
  {
    value: 'troca_session',
    label: 'Troca session',
    description:
      "Sends schedules via Troca's session command API. Only fixed setpoints are supported, so limits are approximated and an active session is required.",
  },
]

interface FormValues {
  baseUrl: string
  basicUser: string
  basicPassword: string
  connectorId: string | null
  readingPollRateSeconds: number | ''
  rampStepSeconds: number | ''
  schedulePollRateSeconds: number | ''
  metadataPollRateSeconds: number | ''
  scheduleSyncMode: ScheduleSyncMode
  ocppConnectorName: string
  ocppVersion: string
  ocppStationName: string
  ocppEvseNb: number | ''
  evseId: string | null
}

function valuesFromConfig(config: TrocaConfig): FormValues {
  return {
    baseUrl: config.baseUrl ?? '',
    basicUser: config.basicUser ?? '',
    basicPassword: '',
    connectorId: config.connectorId,
    readingPollRateSeconds: config.readingPollRateSeconds ?? 20,
    rampStepSeconds: config.rampStepSeconds ?? 3,
    schedulePollRateSeconds: config.schedulePollRateSeconds ?? 10,
    metadataPollRateSeconds: config.metadataPollRateSeconds ?? 30,
    scheduleSyncMode: config.scheduleSyncMode ?? 'ocpp',
    ocppConnectorName: config.ocppConnectorName ?? '',
    ocppVersion: config.ocppVersion ?? '',
    ocppStationName: config.ocppStationName ?? '',
    ocppEvseNb: config.ocppEvseNb ?? '',
    evseId: config.evseId,
  }
}

const EMPTY_CONFIG: TrocaConfig = {
  createdAt: null,
  baseUrl: null,
  basicUser: null,
  hasBasicPassword: false,
  connectorId: null,
  readingPollRateSeconds: null,
  rampStepSeconds: null,
  schedulePollRateSeconds: null,
  metadataPollRateSeconds: null,
  scheduleSyncMode: null,
  ocppConnectorName: null,
  ocppVersion: null,
  ocppStationName: null,
  ocppEvseNb: null,
  evseId: null,
}

/** Works out the discoverable values the poller would otherwise discover for itself - mirrors the discovery
 * logic in cactus_juice.troca.poll (and session_schedule). Anything ambiguous is left out and reported back. */
function discoveredValues(
  discovery: TrocaDiscovery,
  connectorId: string | null,
): { values: Partial<FormValues>; problems: string[] } {
  const values: Partial<FormValues> = {}
  const problems: string[] = []

  const connector = discovery.connectors.find((c) => c.connectorId === connectorId)
  if (!connector) {
    problems.push('select a connector to fill the OCPP connector name/version')
  } else if (!connector.ocppVersion) {
    problems.push(`connector "${connector.name}" isn't an OCPP connector`)
  } else {
    values.ocppConnectorName = connector.name
    values.ocppVersion = connector.ocppVersion
  }

  if (discovery.stations.length === 1) {
    values.ocppStationName = discovery.stations[0].name
  } else {
    problems.push(`${discovery.stations.length} charging stations found - pick one`)
  }

  const stationEvses = values.ocppStationName
    ? discovery.evses.filter((e) => e.stationName === values.ocppStationName)
    : discovery.evses
  if (stationEvses.length === 1) {
    values.evseId = stationEvses[0].evseId
    if (stationEvses[0].evseNb !== null) values.ocppEvseNb = stationEvses[0].evseNb
  } else {
    problems.push(`${stationEvses.length} EVSEs found - pick one`)
  }

  return { values, problems }
}

export function TrocaConfigPage() {
  const queryClient = useQueryClient()

  const { data: config, isLoading, isError, error } = useQuery({
    queryKey: CONFIG_QUERY_KEY,
    queryFn: fetchTrocaConfig,
  })

  // A TrocaConfig must already be on record before we have credentials to query the Troca API with.
  const isConfigured = !!config?.createdAt

  const form = useForm<FormValues>({
    initialValues: valuesFromConfig(EMPTY_CONFIG),
    validate: {
      basicPassword: (value) =>
        !isConfigured && !value.trim() ? 'Required the first time a connection is configured' : null,
      readingPollRateSeconds: (value) => (typeof value === 'number' && value > 0 ? null : 'Must be greater than 0'),
      rampStepSeconds: (value) => (typeof value === 'number' && value > 0 ? null : 'Must be greater than 0'),
      schedulePollRateSeconds: (value) => (typeof value === 'number' && value > 0 ? null : 'Must be greater than 0'),
      metadataPollRateSeconds: (value) => (typeof value === 'number' && value > 0 ? null : 'Must be greater than 0'),
      ocppEvseNb: (value) => (value === '' || (typeof value === 'number' && value >= 0) ? null : 'Must be 0 or more'),
    },
  })

  // Re-seed the form whenever a fresh config loads (initial fetch, or right after a save). basicPassword is
  // intentionally left blank - it's never sent back by the API.
  useEffect(() => {
    if (config) form.setValues(valuesFromConfig(config))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config])

  const discoveryQuery = useQuery({
    queryKey: DISCOVERY_QUERY_KEY,
    queryFn: fetchTrocaDiscovery,
    enabled: false,
    retry: false,
  })

  const mutation = useMutation({
    mutationFn: updateTrocaConfig,
    onSuccess: (updated) => {
      queryClient.setQueryData(CONFIG_QUERY_KEY, updated)
      notifications.show({
        color: 'teal',
        title: 'Saved',
        message: 'Troca configuration updated',
      })
    },
    onError: (err) => {
      notifications.show({
        color: 'red',
        title: 'Save failed',
        message: formatApiError(err),
        autoClose: false,
      })
    },
  })

  const discovery = discoveryQuery.data

  const selectedConnector = discovery?.connectors.find((c) => c.connectorId === form.values.connectorId)
  const connectorOptions = (discovery?.connectors ?? []).map((c) => ({
    value: c.connectorId,
    label: `${c.name} (${c.connectorId})`,
  }))
  // Keep the currently-saved connector selectable even before the list has been (re)fetched.
  if (form.values.connectorId && !connectorOptions.some((o) => o.value === form.values.connectorId)) {
    connectorOptions.push({ value: form.values.connectorId, label: form.values.connectorId })
  }

  const stationOptions = (discovery?.stations ?? []).map((s) => s.name)

  const evseOptions = (discovery?.evses ?? []).map((e) => ({
    value: e.evseId,
    label: `${e.name ?? e.evseId}${e.evseNb !== null ? ` (EVSE #${e.evseNb})` : ''}`,
  }))
  if (form.values.evseId && !evseOptions.some((o) => o.value === form.values.evseId)) {
    evseOptions.push({ value: form.values.evseId, label: form.values.evseId })
  }

  const autoFill = async () => {
    const result = await discoveryQuery.refetch()
    if (!result.data) return // The fetch error is rendered below

    const { values, problems } = discoveredValues(result.data, form.values.connectorId)
    form.setValues(values)
    notifications.show({
      color: problems.length ? 'yellow' : 'teal',
      title: problems.length ? 'Partially filled from Troca' : 'Filled from Troca',
      message: problems.length
        ? `Couldn't fill everything: ${problems.join('; ')}.`
        : 'Review the values and save to pin them.',
    })
  }

  const clearDiscoverables = () =>
    form.setValues({ ocppConnectorName: '', ocppVersion: '', ocppStationName: '', ocppEvseNb: '', evseId: null })

  const selectedMode = SCHEDULE_SYNC_MODES.find((m) => m.value === form.values.scheduleSyncMode)

  return (
    <div style={{ maxWidth: 720 }}>
      <PageHeading
        title="Troca Config"
        subtitle="Connection details used to talk to the Troca API, the active connector and how schedules are synced."
      />

      <Paper withBorder p="lg" pos="relative">
        <LoadingOverlay visible={isLoading} />

        {isError && (
          <Alert color="red" icon={<IconAlertCircle size={16} />} mb="md">
            Failed to load configuration: {formatApiError(error)}
          </Alert>
        )}

        <form
          onSubmit={form.onSubmit((values) =>
            mutation.mutate({
              baseUrl: values.baseUrl,
              basicUser: values.basicUser,
              basicPassword: values.basicPassword,
              connectorId: values.connectorId,
              readingPollRateSeconds: Number(values.readingPollRateSeconds),
              rampStepSeconds: Number(values.rampStepSeconds),
              schedulePollRateSeconds: Number(values.schedulePollRateSeconds),
              metadataPollRateSeconds: Number(values.metadataPollRateSeconds),
              scheduleSyncMode: values.scheduleSyncMode,
              ocppConnectorName: values.ocppConnectorName,
              ocppVersion: values.ocppVersion,
              ocppStationName: values.ocppStationName,
              ocppEvseNb: values.ocppEvseNb === '' ? null : Number(values.ocppEvseNb),
              evseId: values.evseId,
            }),
          )}
        >
          <Stack gap="lg">
            <Stack gap="sm">
              <Text fw={600}>Connection</Text>
              <TextInput
                label="Base URL"
                description="Root endpoint of the Troca API"
                placeholder="https://troca.example.com"
                required
                {...form.getInputProps('baseUrl')}
              />
              <TextInput
                label="Username"
                description="HTTP BASIC username"
                required
                {...form.getInputProps('basicUser')}
              />
              <PasswordInput
                label="Password"
                description={
                  isConfigured
                    ? 'HTTP BASIC password - leave blank to keep the currently stored password'
                    : 'HTTP BASIC password'
                }
                placeholder={isConfigured ? 'Leave blank to keep existing' : undefined}
                required={!isConfigured}
                {...form.getInputProps('basicPassword')}
              />
            </Stack>

            <Divider />

            <Stack gap="sm">
              <Text fw={600}>Polling</Text>
              <Group grow>
                <NumberInput
                  label="Reading poll rate"
                  description="Seconds between polls to the Troca API for readings"
                  min={1}
                  required
                  {...form.getInputProps('readingPollRateSeconds')}
                />
                <NumberInput
                  label="Ramp step"
                  description="Seconds per step in a ramping schedule"
                  min={1}
                  required
                  {...form.getInputProps('rampStepSeconds')}
                />
              </Group>
              <Group grow>
                <NumberInput
                  label="Schedule poll rate"
                  description="Seconds between polls for charge schedule updates"
                  min={1}
                  required
                  {...form.getInputProps('schedulePollRateSeconds')}
                />
                <NumberInput
                  label="Metadata poll rate"
                  description="Seconds between polls for EVSE nameplate ratings"
                  min={1}
                  required
                  {...form.getInputProps('metadataPollRateSeconds')}
                />
              </Group>
            </Stack>

            <Divider />

            <Stack gap="sm">
              <Text fw={600}>Schedule sync</Text>
              <SegmentedControl
                data={SCHEDULE_SYNC_MODES.map(({ value, label }) => ({ value, label }))}
                value={form.values.scheduleSyncMode}
                onChange={(value) => form.setFieldValue('scheduleSyncMode', value as ScheduleSyncMode)}
              />
              {selectedMode && (
                <Text size="xs" c="dimmed">
                  {selectedMode.description}
                </Text>
              )}
            </Stack>

            <Divider />

            <Fieldset legend="Connector" disabled={!isConfigured}>
              <Stack gap="sm">
                <Text size="xs" c="dimmed">
                  {isConfigured
                    ? 'Pick which connector on the Troca API this instance should use.'
                    : 'Save a connection above before a connector can be selected.'}
                </Text>

                <Group align="flex-end" wrap="nowrap" gap="xs">
                  <Select
                    flex={1}
                    label="Connector"
                    placeholder={discovery ? 'Select a connector...' : 'Fetch from Troca to choose one'}
                    data={connectorOptions}
                    value={form.values.connectorId}
                    onChange={(value) => form.setFieldValue('connectorId', value)}
                    searchable
                    clearable
                  />
                  <Button
                    variant="default"
                    leftSection={<IconRefresh size={16} />}
                    onClick={() => discoveryQuery.refetch()}
                    loading={discoveryQuery.isFetching}
                    disabled={!isConfigured}
                  >
                    Fetch from Troca
                  </Button>
                </Group>

                {discoveryQuery.isError && (
                  <Alert color="red" icon={<IconAlertCircle size={16} />}>
                    Failed to fetch from Troca: {formatApiError(discoveryQuery.error)}
                  </Alert>
                )}

                {selectedConnector && (
                  <Group gap="xs">
                    <Badge variant="light">{selectedConnector.connectorType}</Badge>
                    {selectedConnector.ocppVersion && (
                      <Badge variant="light" color="grape">
                        OCPP {selectedConnector.ocppVersion}
                      </Badge>
                    )}
                    {selectedConnector.alternativeNames.map((name) => (
                      <Badge key={name} variant="outline" color="gray">
                        {name}
                      </Badge>
                    ))}
                  </Group>
                )}
              </Stack>
            </Fieldset>

            <Fieldset legend="Pinned Troca details" disabled={!isConfigured}>
              <Stack gap="sm">
                <Text size="xs" c="dimmed">
                  All of these can be discovered via the Troca API - leave a field blank to have it discovered on every
                  poll, or pin it here to skip that discovery.
                </Text>

                <Group grow align="flex-start">
                  <TextInput
                    label="OCPP connector name"
                    description="Troca connector used for the OCPP passthrough"
                    placeholder="Discover from connector"
                    {...form.getInputProps('ocppConnectorName')}
                  />
                  <TextInput
                    label="OCPP version"
                    description="eg 2.1"
                    placeholder="Discover from connector name"
                    {...form.getInputProps('ocppVersion')}
                  />
                </Group>
                <Group grow align="flex-start">
                  <Autocomplete
                    label="Charging station"
                    description="OCPP charging station identity"
                    placeholder="Discover (first station)"
                    data={stationOptions}
                    {...form.getInputProps('ocppStationName')}
                  />
                  <NumberInput
                    label="OCPP EVSE number"
                    description="Used when querying the EVSE's rating"
                    placeholder="Default (1)"
                    min={0}
                    allowDecimal={false}
                    {...form.getInputProps('ocppEvseNb')}
                  />
                </Group>
                <Select
                  label="Troca EVSE"
                  description="Only used in Troca session mode - targeted by session schedule commands"
                  placeholder="Discover from the active session"
                  data={evseOptions}
                  value={form.values.evseId}
                  onChange={(value) => {
                    form.setFieldValue('evseId', value)
                    const evse = discovery?.evses.find((e) => e.evseId === value)
                    if (evse?.evseNb != null) form.setFieldValue('ocppEvseNb', evse.evseNb)
                  }}
                  searchable
                  clearable
                />

                <Group justify="flex-end" gap="xs">
                  <Button variant="subtle" color="gray" leftSection={<IconEraser size={16} />} onClick={clearDiscoverables}>
                    Clear (discover all)
                  </Button>
                  <Button
                    variant="default"
                    leftSection={<IconWand size={16} />}
                    onClick={autoFill}
                    loading={discoveryQuery.isFetching}
                  >
                    Auto-fill from Troca
                  </Button>
                </Group>
              </Stack>
            </Fieldset>

            <Group justify="flex-end">
              <Button type="submit" leftSection={<IconDeviceFloppy size={16} />} loading={mutation.isPending}>
                Save configuration
              </Button>
            </Group>
          </Stack>
        </form>
      </Paper>
    </div>
  )
}
