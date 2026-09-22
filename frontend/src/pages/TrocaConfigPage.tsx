import {
  Alert,
  Badge,
  Button,
  Divider,
  Fieldset,
  Group,
  LoadingOverlay,
  NumberInput,
  Paper,
  PasswordInput,
  Select,
  Stack,
  Text,
  TextInput,
} from '@mantine/core'
import { useForm } from '@mantine/form'
import { notifications } from '@mantine/notifications'
import { IconAlertCircle, IconDeviceFloppy, IconRefresh } from '@tabler/icons-react'
import { useEffect } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { formatApiError } from '../api/http'
import {
  fetchTrocaConfig,
  fetchTrocaConnectors,
  updateTrocaConfig,
  type TrocaConfig,
} from '../api/troca'
import { PageHeading } from '../layout/AppLayout'

const CONFIG_QUERY_KEY = ['troca-config']
const CONNECTORS_QUERY_KEY = ['troca-connectors']

interface FormValues {
  baseUrl: string
  basicUser: string
  basicPassword: string
  connectorId: string | null
  readingPollRateSeconds: number | ''
  rampStepSeconds: number | ''
  schedulePollRateSeconds: number | ''
  metadataPollRateSeconds: number | ''
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
  }
}

export function TrocaConfigPage() {
  const queryClient = useQueryClient()

  const { data: config, isLoading, isError, error } = useQuery({
    queryKey: CONFIG_QUERY_KEY,
    queryFn: fetchTrocaConfig,
  })

  // A TrocaConfig must already be on record before we have credentials to enumerate connectors with.
  const isConfigured = !!config?.createdAt

  const form = useForm<FormValues>({
    initialValues: valuesFromConfig({
      createdAt: null,
      baseUrl: null,
      basicUser: null,
      hasBasicPassword: false,
      connectorId: null,
      readingPollRateSeconds: null,
      rampStepSeconds: null,
      schedulePollRateSeconds: null,
      metadataPollRateSeconds: null,
    }),
    validate: {
      basicPassword: (value) =>
        !isConfigured && !value.trim() ? 'Required the first time a connection is configured' : null,
      readingPollRateSeconds: (value) => (typeof value === 'number' && value > 0 ? null : 'Must be greater than 0'),
      rampStepSeconds: (value) => (typeof value === 'number' && value > 0 ? null : 'Must be greater than 0'),
      schedulePollRateSeconds: (value) => (typeof value === 'number' && value > 0 ? null : 'Must be greater than 0'),
      metadataPollRateSeconds: (value) => (typeof value === 'number' && value > 0 ? null : 'Must be greater than 0'),
    },
  })

  // Re-seed the form whenever a fresh config loads (initial fetch, or right after a save). basicPassword is
  // intentionally left blank - it's never sent back by the API.
  useEffect(() => {
    if (config) form.setValues(valuesFromConfig(config))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config])

  const connectorsQuery = useQuery({
    queryKey: CONNECTORS_QUERY_KEY,
    queryFn: fetchTrocaConnectors,
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

  const selectedConnector = connectorsQuery.data?.find((c) => c.connectorId === form.values.connectorId)
  const connectorOptions = (connectorsQuery.data ?? []).map((c) => ({
    value: c.connectorId,
    label: `${c.name} (${c.connectorId})`,
  }))
  // Keep the currently-saved connector selectable even before the list has been (re)fetched.
  if (form.values.connectorId && !connectorOptions.some((o) => o.value === form.values.connectorId)) {
    connectorOptions.push({ value: form.values.connectorId, label: form.values.connectorId })
  }

  return (
    <div style={{ maxWidth: 720 }}>
      <PageHeading title="Troca Config" subtitle="Connection details used to talk to the Troca API, and the active connector." />

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
                    placeholder={connectorsQuery.data ? 'Select a connector...' : 'Fetch connectors to choose one'}
                    data={connectorOptions}
                    value={form.values.connectorId}
                    onChange={(value) => form.setFieldValue('connectorId', value)}
                    searchable
                    clearable
                  />
                  <Button
                    variant="default"
                    leftSection={<IconRefresh size={16} />}
                    onClick={() => connectorsQuery.refetch()}
                    loading={connectorsQuery.isFetching}
                    disabled={!isConfigured}
                  >
                    Fetch connectors
                  </Button>
                </Group>

                {connectorsQuery.isError && (
                  <Alert color="red" icon={<IconAlertCircle size={16} />}>
                    Failed to fetch connectors: {formatApiError(connectorsQuery.error)}
                  </Alert>
                )}

                {selectedConnector && (
                  <Group gap="xs">
                    <Badge variant="light">{selectedConnector.connectorType}</Badge>
                    {selectedConnector.alternativeNames.map((name) => (
                      <Badge key={name} variant="outline" color="gray">
                        {name}
                      </Badge>
                    ))}
                  </Group>
                )}
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
