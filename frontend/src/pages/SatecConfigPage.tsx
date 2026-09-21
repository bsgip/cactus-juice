import {
  ActionIcon,
  Alert,
  Button,
  Divider,
  Group,
  LoadingOverlay,
  NumberInput,
  Paper,
  Select,
  SegmentedControl,
  Stack,
  Switch,
  Text,
  TextInput,
} from '@mantine/core'
import { useForm } from '@mantine/form'
import { notifications } from '@mantine/notifications'
import { IconAlertCircle, IconDeviceFloppy, IconPlus, IconTrash } from '@tabler/icons-react'
import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { formatApiError } from '../api/http'
import {
  createSatecConfig,
  deleteSatecConfig,
  fetchSatecConfigs,
  updateSatecConfig,
  type SatecConfig,
  type SatecConfigInput,
  type SatecModel,
  type SatecParity,
} from '../api/satec'
import { PageHeading } from '../layout/AppLayout'

const SATEC_CONFIGS_QUERY_KEY = ['satec-config']

const MODEL_OPTIONS: { value: SatecModel; label: string }[] = [
  { value: 'em133', label: 'EM133 / EM133-XM' },
  { value: 'em235', label: 'EM235 / PM335 PRO' },
]

const PARITY_OPTIONS: { value: SatecParity; label: string }[] = [
  { value: 'N', label: 'None' },
  { value: 'E', label: 'Even' },
  { value: 'O', label: 'Odd' },
]

type ConnectionType = 'rtu' | 'tcp'

interface FormValues {
  label: string
  pollRateSeconds: number | ''
  model: SatecModel
  connectionType: ConnectionType
  host: string
  port: string
  portTcp: number | ''
  unit: number | ''
  baud: number | ''
  parity: SatecParity
  timeoutSeconds: number | ''
  includePhases: boolean
  includeEnergy: boolean
  floatMode: boolean
}

const BLANK_VALUES: FormValues = {
  label: '',
  pollRateSeconds: 5,
  model: 'em133',
  connectionType: 'rtu',
  host: '',
  port: '/dev/ttyUSB0',
  portTcp: 502,
  unit: 1,
  baud: 19200,
  parity: 'N',
  timeoutSeconds: 1,
  includePhases: false,
  includeEnergy: false,
  floatMode: false,
}

function valuesFromConfig(config: SatecConfig): FormValues {
  return {
    label: config.label,
    pollRateSeconds: config.pollRateSeconds,
    model: config.model,
    connectionType: config.host ? 'tcp' : 'rtu',
    host: config.host ?? '',
    port: config.port ?? '/dev/ttyUSB0',
    portTcp: config.portTcp,
    unit: config.unit,
    baud: config.baud,
    parity: config.parity,
    timeoutSeconds: config.timeoutSeconds,
    includePhases: config.includePhases,
    includeEnergy: config.includeEnergy,
    floatMode: config.floatMode,
  }
}

function toInput(values: FormValues): SatecConfigInput {
  return {
    label: values.label,
    pollRateSeconds: Number(values.pollRateSeconds),
    model: values.model,
    host: values.connectionType === 'tcp' ? values.host : null,
    port: values.connectionType === 'rtu' ? values.port : null,
    portTcp: Number(values.portTcp),
    unit: Number(values.unit),
    baud: Number(values.baud),
    parity: values.parity,
    timeoutSeconds: Number(values.timeoutSeconds),
    includePhases: values.includePhases,
    includeEnergy: values.includeEnergy,
    floatMode: values.floatMode,
  }
}

function SatecConfigCard({
  config,
  onSaved,
  onCancel,
}: {
  config: SatecConfig | null
  onSaved: () => void
  onCancel?: () => void
}) {
  const isNew = config === null
  const [confirmingDelete, setConfirmingDelete] = useState(false)

  const form = useForm<FormValues>({
    initialValues: config ? valuesFromConfig(config) : BLANK_VALUES,
    validate: {
      label: (value) => (value.trim() ? null : 'Required'),
      pollRateSeconds: (value) => (typeof value === 'number' && value > 0 ? null : 'Must be greater than 0'),
    },
  })

  const saveMutation = useMutation({
    mutationFn: (values: FormValues) =>
      isNew ? createSatecConfig(toInput(values)) : updateSatecConfig(config.id, toInput(values)),
    onSuccess: () => {
      notifications.show({ color: 'teal', title: 'Saved', message: `"${form.values.label}" saved` })
      onSaved()
    },
    onError: (err) => {
      notifications.show({ color: 'red', title: 'Save failed', message: formatApiError(err), autoClose: false })
    },
  })

  const deleteMutation = useMutation({
    mutationFn: () => deleteSatecConfig(config!.id),
    onSuccess: () => {
      notifications.show({ color: 'teal', title: 'Deleted', message: `"${config!.label}" removed` })
      onSaved()
    },
    onError: (err) => {
      notifications.show({ color: 'red', title: 'Delete failed', message: formatApiError(err), autoClose: false })
    },
  })

  return (
    <Paper withBorder p="lg" pos="relative">
      <LoadingOverlay visible={saveMutation.isPending || deleteMutation.isPending} />
      <form onSubmit={form.onSubmit((values) => saveMutation.mutate(values))}>
        <Stack gap="md">
          <Group justify="space-between" wrap="nowrap">
            <TextInput
              flex={1}
              label="Label"
              placeholder="e.g. Site meter 1"
              required
              {...form.getInputProps('label')}
            />
            {!isNew && confirmingDelete && (
              <Group gap={4} mt={24} wrap="nowrap">
                <Button color="red" size="xs" onClick={() => deleteMutation.mutate()}>
                  Confirm delete
                </Button>
                <Button variant="default" size="xs" onClick={() => setConfirmingDelete(false)}>
                  Cancel
                </Button>
              </Group>
            )}
            {!isNew && !confirmingDelete && (
              <ActionIcon
                color="red"
                variant="subtle"
                size="lg"
                mt={24}
                onClick={() => setConfirmingDelete(true)}
                title="Delete this SatecConfig"
              >
                <IconTrash size={18} />
              </ActionIcon>
            )}
          </Group>

          <Group grow>
            <NumberInput
              label="Poll rate"
              description="Seconds between samples"
              min={0.1}
              step={0.5}
              required
              {...form.getInputProps('pollRateSeconds')}
            />
            <Select
              label="Meter model"
              data={MODEL_OPTIONS}
              allowDeselect={false}
              {...form.getInputProps('model')}
            />
          </Group>

          <Divider label="Connection" labelPosition="left" />

          <SegmentedControl
            value={form.values.connectionType}
            onChange={(value) => form.setFieldValue('connectionType', value as ConnectionType)}
            data={[
              { label: 'Serial (RTU)', value: 'rtu' },
              { label: 'Modbus/TCP', value: 'tcp' },
            ]}
          />

          {form.values.connectionType === 'rtu' ? (
            <Group grow>
              <TextInput label="Serial device" placeholder="/dev/ttyUSB0" {...form.getInputProps('port')} />
              <NumberInput label="Baud rate" min={1} {...form.getInputProps('baud')} />
              <Select label="Parity" data={PARITY_OPTIONS} allowDeselect={false} {...form.getInputProps('parity')} />
            </Group>
          ) : (
            <Group grow>
              <TextInput label="Host" placeholder="10.0.0.5" required {...form.getInputProps('host')} />
              <NumberInput label="TCP port" min={1} max={65535} {...form.getInputProps('portTcp')} />
            </Group>
          )}

          <Group grow>
            <NumberInput
              label="Modbus unit"
              description="Address 1-247"
              min={1}
              max={247}
              {...form.getInputProps('unit')}
            />
            <NumberInput label="Timeout" description="Seconds" min={0.1} step={0.5} {...form.getInputProps('timeoutSeconds')} />
          </Group>

          <Divider label="Sampling" labelPosition="left" />

          <Group>
            <Switch label="Include phases" {...form.getInputProps('includePhases', { type: 'checkbox' })} />
            <Switch label="Include energy" {...form.getInputProps('includeEnergy', { type: 'checkbox' })} />
            <Switch
              label="Float32 registers"
              description="Meter is already configured for float32"
              {...form.getInputProps('floatMode', { type: 'checkbox' })}
            />
          </Group>

          <Group justify="flex-end">
            {isNew && onCancel && (
              <Button variant="default" onClick={onCancel}>
                Cancel
              </Button>
            )}
            <Button type="submit" leftSection={<IconDeviceFloppy size={16} />} loading={saveMutation.isPending}>
              {isNew ? 'Create' : 'Save'}
            </Button>
          </Group>
        </Stack>
      </form>
    </Paper>
  )
}

export function SatecConfigPage() {
  const queryClient = useQueryClient()
  const [draftKeys, setDraftKeys] = useState<number[]>([])
  const nextDraftKey = useRef(0)

  const { data: configs, isLoading, isError, error } = useQuery({
    queryKey: SATEC_CONFIGS_QUERY_KEY,
    queryFn: fetchSatecConfigs,
  })

  const addDraft = () => {
    nextDraftKey.current += 1
    setDraftKeys((keys) => [...keys, nextDraftKey.current])
  }

  const removeDraft = (key: number) => {
    setDraftKeys((keys) => keys.filter((k) => k !== key))
  }

  const handleSaved = () => {
    queryClient.invalidateQueries({ queryKey: SATEC_CONFIGS_QUERY_KEY })
  }

  return (
    <div style={{ maxWidth: 820 }}>
      <PageHeading
        title="Satec Config"
        subtitle="Connection details for each SATEC meter being polled over Modbus. Add as many as you have meters."
      />

      {isError && (
        <Alert color="red" icon={<IconAlertCircle size={16} />} mb="md">
          Failed to load configuration: {formatApiError(error)}
        </Alert>
      )}

      <Stack gap="lg" pos="relative" mih={isLoading ? 120 : undefined}>
        <LoadingOverlay visible={isLoading} />

        {configs?.length === 0 && draftKeys.length === 0 && (
          <Text c="dimmed" size="sm">
            No meters configured yet - add one below.
          </Text>
        )}

        {configs?.map((config) => (
          <SatecConfigCard key={config.id} config={config} onSaved={handleSaved} />
        ))}

        {draftKeys.map((key) => (
          <SatecConfigCard
            key={`draft-${key}`}
            config={null}
            onSaved={() => {
              removeDraft(key)
              handleSaved()
            }}
            onCancel={() => removeDraft(key)}
          />
        ))}

        <Group justify="flex-start">
          <Button variant="light" leftSection={<IconPlus size={16} />} onClick={addDraft}>
            Add meter
          </Button>
        </Group>
      </Stack>
    </div>
  )
}
