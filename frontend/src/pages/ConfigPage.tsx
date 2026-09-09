import {
  Alert,
  Button,
  Divider,
  Group,
  LoadingOverlay,
  NumberInput,
  Paper,
  Stack,
  Switch,
  Text,
  TextInput,
} from '@mantine/core'
import { useForm } from '@mantine/form'
import { notifications } from '@mantine/notifications'
import { IconAlertCircle, IconDeviceFloppy } from '@tabler/icons-react'
import { useEffect } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { formatApiError } from '../api/http'
import { fetchCsipausConfig, updateCsipausConfig, type CSIPAusConfig } from '../api/config'
import { PageHeading } from '../layout/AppLayout'
import { PemFileField } from './PemFileField'

const CONFIG_QUERY_KEY = ['csipaus-config']

interface FormValues {
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

function valuesFromConfig(config: CSIPAusConfig): FormValues {
  return {
    isAggregator: config.isAggregator,
    nmi: config.nmi ?? '',
    clientPen: config.clientPen !== null ? String(config.clientPen) : '',
    dcapUri: config.dcapUri ?? '',
    verifyHostname: config.verifyHostname,
    verifySsl: config.verifySsl,
    certificatePemFile: null,
    keyPemFile: null,
    sercaPemFile: null,
    clearCertificatePem: false,
    clearKeyPem: false,
    clearSercaPem: false,
  }
}

export function ConfigPage() {
  const queryClient = useQueryClient()

  const { data: config, isLoading, isError, error } = useQuery({
    queryKey: CONFIG_QUERY_KEY,
    queryFn: fetchCsipausConfig,
  })

  const form = useForm<FormValues>({
    initialValues: valuesFromConfig({
      createdAt: null,
      isAggregator: true,
      nmi: null,
      clientPen: null,
      dcapUri: null,
      verifyHostname: true,
      verifySsl: true,
      certificatePem: null,
      sercaPem: null,
      hasKeyPem: false,
    }),
  })

  // Re-seed the form whenever a fresh config loads (initial fetch, or right after a save).
  useEffect(() => {
    if (config) form.setValues(valuesFromConfig(config))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config])

  const mutation = useMutation({
    mutationFn: updateCsipausConfig,
    onSuccess: (updated) => {
      queryClient.setQueryData(CONFIG_QUERY_KEY, updated)
      notifications.show({
        color: 'teal',
        title: 'Saved',
        message: 'CSIP-Aus configuration updated',
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

  return (
    <div style={{ maxWidth: 720 }}>
      <PageHeading
        title="CSIP-Aus Config"
        subtitle="Client identity and utility server connection details used to talk CSIP-Aus."
      />

      <Paper withBorder p="lg" pos="relative">
        <LoadingOverlay visible={isLoading} />

        {isError && (
          <Alert color="red" icon={<IconAlertCircle size={16} />} mb="md">
            Failed to load configuration: {formatApiError(error)}
          </Alert>
        )}

        <form onSubmit={form.onSubmit((values) => mutation.mutate(values))}>
          <Stack gap="lg">
            <Stack gap="sm">
              <Text fw={600}>Client identity</Text>
              <Switch
                label="Aggregator client"
                description="On: this client represents an aggregator managing an EndDevice on someone's behalf. Off: device client."
                {...form.getInputProps('isAggregator', { type: 'checkbox' })}
              />
              <TextInput label="NMI" description="National Metering Identifier for the connection point" {...form.getInputProps('nmi')} />
              <NumberInput
                label="Client PEN"
                description="Private Enterprise Number used to encode mRIDs (defaults to 1 if unset)"
                min={0}
                allowDecimal={false}
                value={form.values.clientPen === '' ? '' : Number(form.values.clientPen)}
                onChange={(value) => form.setFieldValue('clientPen', value === '' ? '' : String(value))}
              />
            </Stack>

            <Divider />

            <Stack gap="sm">
              <Text fw={600}>Utility server</Text>
              <TextInput
                label="DeviceCapability URI"
                description="Root CSIP-Aus endpoint on the utility server"
                placeholder="https://utility.example.com/dcap"
                {...form.getInputProps('dcapUri')}
              />
              <Switch label="Verify hostname" {...form.getInputProps('verifyHostname', { type: 'checkbox' })} />
              <Switch
                label="Verify SSL"
                description="Validate the server's certificate against the SERCA cert below"
                {...form.getInputProps('verifySsl', { type: 'checkbox' })}
              />
            </Stack>

            <Divider />

            <Stack gap="md">
              <Text fw={600}>Certificates</Text>

              <PemFileField
                label="Client certificate"
                description="PEM encoded X.509 client certificate presented for mTLS"
                isCurrentlySet={!!config?.certificatePem}
                preview={config?.certificatePem}
                file={form.values.certificatePemFile}
                onChange={(file) => form.setFieldValue('certificatePemFile', file)}
                cleared={form.values.clearCertificatePem}
                onClearedChange={(cleared) => form.setFieldValue('clearCertificatePem', cleared)}
              />

              <PemFileField
                label="Client private key"
                description="PEM encoded private key matching the client certificate - never shown once stored"
                isCurrentlySet={!!config?.hasKeyPem}
                file={form.values.keyPemFile}
                onChange={(file) => form.setFieldValue('keyPemFile', file)}
                cleared={form.values.clearKeyPem}
                onClearedChange={(cleared) => form.setFieldValue('clearKeyPem', cleared)}
              />

              <PemFileField
                label="SERCA certificate"
                description="PEM encoded CA certificate used to verify the utility server (required if Verify SSL is on)"
                isCurrentlySet={!!config?.sercaPem}
                preview={config?.sercaPem}
                file={form.values.sercaPemFile}
                onChange={(file) => form.setFieldValue('sercaPemFile', file)}
                cleared={form.values.clearSercaPem}
                onClearedChange={(cleared) => form.setFieldValue('clearSercaPem', cleared)}
              />
            </Stack>

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
