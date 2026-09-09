import { AppShell, Group, NavLink, Text, Title } from '@mantine/core'
import { IconChartLine, IconPlugConnected, IconSettings } from '@tabler/icons-react'
import { Link, Outlet, useLocation } from 'react-router-dom'

/** Left-hand navigation entries. Telemetry/charting pages will slot in here as they're built. */
const NAV_ITEMS = [
  { to: '/config', label: 'CSIP-Aus Config', icon: IconSettings },
  { to: '/telemetry', label: 'Telemetry', icon: IconChartLine, disabled: true },
]

export function AppLayout() {
  const location = useLocation()

  return (
    <AppShell header={{ height: 56 }} navbar={{ width: 240, breakpoint: 'sm' }} padding="lg">
      <AppShell.Header>
        <Group h="100%" px="md" gap="xs">
          <IconPlugConnected size={22} />
          <Title order={4} fw={600}>
            Cactus Juice
          </Title>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar p="sm">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            component={Link}
            to={item.to}
            label={item.label}
            leftSection={<item.icon size={18} stroke={1.5} />}
            active={location.pathname.startsWith(item.to)}
            disabled={item.disabled}
            description={item.disabled ? 'Coming soon' : undefined}
          />
        ))}
      </AppShell.Navbar>

      <AppShell.Main>
        <Outlet />
      </AppShell.Main>
    </AppShell>
  )
}

export function PageHeading({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <div style={{ marginBottom: 'var(--mantine-spacing-lg)' }}>
      <Title order={2}>{title}</Title>
      {subtitle ? (
        <Text c="dimmed" size="sm">
          {subtitle}
        </Text>
      ) : null}
    </div>
  )
}
