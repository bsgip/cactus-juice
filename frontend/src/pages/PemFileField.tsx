import { Badge, FileInput, Group, Stack, Text, UnstyledButton } from '@mantine/core'
import { IconFileCertificate, IconX } from '@tabler/icons-react'

/** A file-upload control for one of the three CSIP-Aus PEM files, showing whether a value is
 * currently stored server-side and letting the user replace or clear it. */
export function PemFileField({
  label,
  description,
  isCurrentlySet,
  preview,
  file,
  onChange,
  cleared,
  onClearedChange,
}: {
  label: string
  description: string
  isCurrentlySet: boolean
  /** Optional PEM text to show a read-only preview of (certs are not sensitive; private keys never are). */
  preview?: string | null
  file: File | null
  onChange: (file: File | null) => void
  cleared: boolean
  onClearedChange: (cleared: boolean) => void
}) {
  const showAsSet = (isCurrentlySet && !cleared) || file !== null

  return (
    <Stack gap={4}>
      <Group justify="space-between" wrap="nowrap">
        <Text fw={500} size="sm">
          {label}
        </Text>
        <Badge
          color={showAsSet ? 'teal' : 'gray'}
          variant="light"
          leftSection={<IconFileCertificate size={12} />}
        >
          {showAsSet ? 'Configured' : 'Not set'}
        </Badge>
      </Group>
      <Text size="xs" c="dimmed">
        {description}
      </Text>

      <Group align="flex-end" wrap="nowrap" gap="xs">
        <FileInput
          flex={1}
          placeholder={isCurrentlySet && !cleared ? 'Replace file...' : 'Upload file...'}
          accept=".pem,.crt,.key,application/x-pem-file,text/plain"
          value={file}
          onChange={(newFile) => {
            onChange(newFile)
            if (newFile) onClearedChange(false)
          }}
          clearable
        />
        {isCurrentlySet && !cleared && !file && (
          <UnstyledButton
            onClick={() => onClearedChange(true)}
            c="red"
            style={{ display: 'flex', alignItems: 'center', gap: 4 }}
            title={`Remove stored ${label}`}
          >
            <IconX size={16} />
            <Text size="xs">Remove</Text>
          </UnstyledButton>
        )}
        {cleared && (
          <UnstyledButton onClick={() => onClearedChange(false)} style={{ display: 'flex', alignItems: 'center' }}>
            <Text size="xs" c="dimmed">
              Undo remove
            </Text>
          </UnstyledButton>
        )}
      </Group>

      {preview && !cleared && !file && (
        <Text
          component="pre"
          size="xs"
          c="dimmed"
          style={{
            maxHeight: 90,
            overflow: 'auto',
            padding: 'var(--mantine-spacing-xs)',
            background: 'var(--mantine-color-default-hover)',
            borderRadius: 'var(--mantine-radius-sm)',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-all',
          }}
        >
          {preview}
        </Text>
      )}
    </Stack>
  )
}
