import { Alert, Badge, Group, Paper, SimpleGrid, Stack, Text } from '@mantine/core'
import { LineChart, type LineChartSeries } from '@mantine/charts'
import { IconAlertCircle, IconHeartbeat } from '@tabler/icons-react'
import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'

import { formatApiError } from '../api/http'
import {
  fetchTelemetrySnapshot,
  type DynamicPrice,
  type OcppReading,
  type SatecReading,
  type ScheduledValues,
} from '../api/telemetry'
import { PageHeading } from '../layout/AppLayout'

const SNAPSHOT_QUERY_KEY = ['telemetry-snapshot']
const REFRESH_INTERVAL_MS = 10_000

/** Colors cycled (in this fixed order) across whichever SatecConfig labels are present - deliberately disjoint
 * from the static series colors used elsewhere in each panel. */
const SATEC_COLOR_PALETTE = ['pink.6', 'lime.7', 'orange.7', 'red.6', 'green.7', 'dark.4']

/** The chart data key a given SatecConfig label's series is plotted under - shared between building the
 * interval data and building the matching LineChartSeries definition. */
function satecSeriesKey(label: string): string {
  return `satec:${label}`
}

/** A flat [start, end) span carrying one series' value - the shared shape every data source below is
 * normalised into before being merged into chart rows. */
interface Interval {
  start: number
  end: number | null
  value: number | null
}

function valueAt(intervals: Interval[], t: number): number | null {
  for (const iv of intervals) {
    if (t >= iv.start && (iv.end === null || t < iv.end)) return iv.value
  }
  return null
}

/** Merges any number of per-series interval sets into a single array of chart rows - one row per moment any
 * series changes value, clipped to [windowStartMs, windowEndMs]. Rendered with curveType="stepAfter" so each
 * row holds its value flat until the next one, matching the [start, end) semantics of the source data. */
function buildStepRows(
  seriesIntervals: Record<string, Interval[]>,
  windowStartMs: number,
  windowEndMs: number,
): Record<string, number | null>[] {
  const breakpoints = new Set<number>([windowStartMs, windowEndMs])
  for (const intervals of Object.values(seriesIntervals)) {
    for (const iv of intervals) {
      if (iv.start > windowStartMs && iv.start < windowEndMs) breakpoints.add(iv.start)
      if (iv.end !== null && iv.end > windowStartMs && iv.end < windowEndMs) breakpoints.add(iv.end)
    }
  }

  return Array.from(breakpoints)
    .sort((a, b) => a - b)
    .map((time) => {
      const row: Record<string, number | null> = { time }
      for (const [key, intervals] of Object.entries(seriesIntervals)) {
        row[key] = valueAt(intervals, time)
      }
      return row
    })
}

function scheduleToIntervals(schedule: ScheduledValues[], field: keyof ScheduledValues): Interval[] {
  return schedule.map((s) => ({
    start: new Date(s.activeFrom).getTime(),
    end: s.activeTo ? new Date(s.activeTo).getTime() : null,
    value: s[field] as number | null,
  }))
}

/** OCPP readings are instantaneous samples - each one's value is treated as holding until the next sample
 * arrives (or the end of the window, for the most recent one). */
function readingsToIntervals(readings: OcppReading[], field: keyof OcppReading, windowEndMs: number): Interval[] {
  return readings.map((r, i) => ({
    start: new Date(r.readingStart).getTime(),
    end: i + 1 < readings.length ? new Date(readings[i + 1].readingStart).getTime() : windowEndMs,
    value: r[field] as number | null,
  }))
}

/** Groups SatecReadings by their parent SatecConfig label (each meter reports independently, so readings from
 * different meters must be charted as separate series rather than merged) and sorts each group by time. */
function groupSatecReadingsByLabel(readings: SatecReading[]): Map<string, SatecReading[]> {
  const groups = new Map<string, SatecReading[]>()
  for (const r of readings) {
    const group = groups.get(r.label)
    if (group) group.push(r)
    else groups.set(r.label, [r])
  }
  for (const group of groups.values()) {
    group.sort((a, b) => new Date(a.readingStart).getTime() - new Date(b.readingStart).getTime())
  }
  return groups
}

/** Same "hold until the next sample" semantics as readingsToIntervals, for a single meter's readings - scale
 * converts the SatecReading's native units (kW/kVAr) up to the W/VAr used by the OCPP-sourced series it's
 * plotted alongside. */
function satecReadingsToIntervals(
  readings: SatecReading[],
  field: keyof Omit<SatecReading, 'label' | 'readingStart'>,
  windowEndMs: number,
  scale: number,
): Interval[] {
  return readings.map((r, i) => ({
    start: new Date(r.readingStart).getTime(),
    end: i + 1 < readings.length ? new Date(readings[i + 1].readingStart).getTime() : windowEndMs,
    value: r[field] * scale,
  }))
}

/** Dynamic prices can overlap (multiple tariff primacies active at once) - at each moment, the row with the
 * lowest primacy wins (ties broken by the most recently started), mirroring
 * cactus_juice.csipaus.controls.active_interval_to_control_values's precedence order. */
function pricesToIntervals(prices: DynamicPrice[], windowStartMs: number, windowEndMs: number): Interval[] {
  const boundarySet = new Set<number>([windowStartMs, windowEndMs])
  for (const p of prices) {
    const start = new Date(p.startedAt).getTime()
    const end = new Date(p.finishedAt).getTime()
    if (start > windowStartMs && start < windowEndMs) boundarySet.add(start)
    if (end > windowStartMs && end < windowEndMs) boundarySet.add(end)
  }
  const boundaries = Array.from(boundarySet).sort((a, b) => a - b)

  const result: Interval[] = []
  for (let i = 0; i < boundaries.length - 1; i++) {
    const t = boundaries[i]
    const active = prices
      .filter((p) => t >= new Date(p.startedAt).getTime() && t < new Date(p.finishedAt).getTime())
      .sort((a, b) => a.primacy - b.primacy || new Date(b.startedAt).getTime() - new Date(a.startedAt).getTime())
    result.push({ start: t, end: boundaries[i + 1], value: active.length ? active[0].priceKwh : null })
  }
  return result
}

function formatClockTime(ms: number): string {
  return new Date(ms).toLocaleTimeString([], { hour12: false })
}

interface TimeSeriesPanelProps {
  title: string
  unit: string
  data: Record<string, number | null>[]
  series: LineChartSeries[]
  windowStartMs: number
  windowEndMs: number
  nowMs: number
  yAxisDomain?: [number, number]
  extraReferenceLines?: { y: number; label: string }[]
}

function TimeSeriesPanel({
  title,
  unit,
  data,
  series,
  windowStartMs,
  windowEndMs,
  nowMs,
  yAxisDomain,
  extraReferenceLines = [],
}: TimeSeriesPanelProps) {
  return (
    <Paper withBorder p="md">
      <Text fw={600} size="sm" mb="xs">
        {title}
      </Text>
      <LineChart
        h={220}
        data={data}
        dataKey="time"
        series={series}
        curveType="stepAfter"
        withLegend
        withDots={false}
        strokeWidth={2}
        unit={unit}
        connectNulls={false}
        xAxisProps={{
          type: 'number',
          domain: [windowStartMs, windowEndMs],
          tickFormatter: formatClockTime,
        }}
        yAxisProps={yAxisDomain ? { domain: yAxisDomain } : undefined}
        tooltipProps={{ labelFormatter: (label) => formatClockTime(Number(label)) }}
        referenceLines={[
          { x: nowMs, color: 'red.6', label: 'Now', labelPosition: 'insideTopLeft' },
          ...extraReferenceLines.map((rl) => ({
            y: rl.y,
            color: 'gray.5',
            strokeDasharray: '4 4',
            label: rl.label,
          })),
        ]}
      />
    </Paper>
  )
}

function MetadataRow({ label, value }: { label: string; value: string }) {
  return (
    <Group justify="space-between" gap="xs">
      <Text size="sm" c="dimmed">
        {label}
      </Text>
      <Text size="sm" fw={500}>
        {value}
      </Text>
    </Group>
  )
}

function formatNumber(value: number | null, suffix: string): string {
  return value === null ? '—' : `${value.toLocaleString()} ${suffix}`
}

export function TelemetryPage() {
  const { data: snapshot, isLoading, isError, error } = useQuery({
    queryKey: SNAPSHOT_QUERY_KEY,
    queryFn: fetchTelemetrySnapshot,
    refetchInterval: REFRESH_INTERVAL_MS,
  })

  const chartData = useMemo(() => {
    if (!snapshot) return null

    const windowStartMs = new Date(snapshot.windowStart).getTime()
    const windowEndMs = new Date(snapshot.windowEnd).getTime()
    const nowMs = new Date(snapshot.now).getTime()

    // Each SatecConfig (meter) gets its own series in the panels below - grouped and sorted by label so
    // color assignment is stable across refreshes regardless of the order readings come back in.
    const satecByLabel = groupSatecReadingsByLabel(snapshot.satecReadings)
    const satecLabels = Array.from(satecByLabel.keys()).sort()

    const satecPowerIntervals: Record<string, Interval[]> = {}
    const satecReactivePowerIntervals: Record<string, Interval[]> = {}
    const satecFrequencyIntervals: Record<string, Interval[]> = {}
    const satecVoltageIntervals: Record<string, Interval[]> = {}
    for (const label of satecLabels) {
      const readings = satecByLabel.get(label)!
      const key = satecSeriesKey(label)
      satecPowerIntervals[key] = satecReadingsToIntervals(readings, 'totalKw', windowEndMs, 1000)
      satecReactivePowerIntervals[key] = satecReadingsToIntervals(readings, 'totalKvar', windowEndMs, 1000)
      satecFrequencyIntervals[key] = satecReadingsToIntervals(readings, 'frequency', windowEndMs, 1)
      satecVoltageIntervals[key] = satecReadingsToIntervals(readings, 'vAvgLn', windowEndMs, 1)
    }

    const power = buildStepRows(
      {
        importLimitWatts: scheduleToIntervals(snapshot.schedule, 'importLimitWatts'),
        exportLimitWatts: scheduleToIntervals(snapshot.schedule, 'exportLimitWatts'),
        loadLimitWatts: scheduleToIntervals(snapshot.schedule, 'loadLimitWatts'),
        generationLimitWatts: scheduleToIntervals(snapshot.schedule, 'generationLimitWatts'),
        storageTargetWatts: scheduleToIntervals(snapshot.schedule, 'storageTargetWatts'),
        importActivePowerWatts: readingsToIntervals(snapshot.ocppReadings, 'importActivePowerWatts', windowEndMs),
        exportActivePowerWatts: readingsToIntervals(snapshot.ocppReadings, 'exportActivePowerWatts', windowEndMs),
        ...satecPowerIntervals,
      },
      windowStartMs,
      windowEndMs,
    )

    const reactivePower = buildStepRows(
      {
        importReactivePowerVar: readingsToIntervals(snapshot.ocppReadings, 'importReactivePowerVar', windowEndMs),
        exportReactivePowerVar: readingsToIntervals(snapshot.ocppReadings, 'exportReactivePowerVar', windowEndMs),
        ...satecReactivePowerIntervals,
      },
      windowStartMs,
      windowEndMs,
    )

    const price = buildStepRows(
      { priceKwh: pricesToIntervals(snapshot.dynamicPrices, windowStartMs, windowEndMs) },
      windowStartMs,
      windowEndMs,
    )

    const frequency = buildStepRows(
      {
        frequencyHz: readingsToIntervals(snapshot.ocppReadings, 'frequencyHz', windowEndMs),
        ...satecFrequencyIntervals,
      },
      windowStartMs,
      windowEndMs,
    )

    const voltage = buildStepRows(
      {
        voltageVolts: readingsToIntervals(snapshot.ocppReadings, 'voltageVolts', windowEndMs),
        ...satecVoltageIntervals,
      },
      windowStartMs,
      windowEndMs,
    )

    const soc = buildStepRows(
      { socPercent: readingsToIntervals(snapshot.ocppReadings, 'socPercent', windowEndMs) },
      windowStartMs,
      windowEndMs,
    )

    return { windowStartMs, windowEndMs, nowMs, power, reactivePower, price, frequency, voltage, soc, satecLabels }
  }, [snapshot])

  const satecSeries = useMemo(
    () =>
      (chartData?.satecLabels ?? []).map((label, i) => ({
        name: satecSeriesKey(label),
        label,
        color: SATEC_COLOR_PALETTE[i % SATEC_COLOR_PALETTE.length],
      })),
    [chartData],
  )

  return (
    <Stack gap="lg">
      <PageHeading
        title="Telemetry"
        subtitle="Live schedule, pricing, OCPP and Satec meter readings - refreshes every 10 seconds"
      />

      {isError ? (
        <Alert color="red" icon={<IconAlertCircle size={18} />} title="Failed to load telemetry">
          {formatApiError(error)}
        </Alert>
      ) : null}

      <SimpleGrid cols={{ base: 1, md: 2 }}>
        <Paper withBorder p="md">
          <Text fw={600} size="sm" mb="xs">
            EVSE metadata
          </Text>
          {snapshot?.ocppMetadata ? (
            <Stack gap={6}>
              <MetadataRow label="Recorded at" value={new Date(snapshot.ocppMetadata.createdAt).toLocaleString()} />
              <MetadataRow label="Max voltage" value={formatNumber(snapshot.ocppMetadata.maxVoltageVolts, 'V')} />
              <MetadataRow label="Min voltage" value={formatNumber(snapshot.ocppMetadata.minVoltageVolts, 'V')} />
              <MetadataRow label="Max power" value={formatNumber(snapshot.ocppMetadata.maxPowerWatts, 'W')} />
              <MetadataRow
                label="Max charge rate"
                value={formatNumber(snapshot.ocppMetadata.maxChargeRateWatts, 'W')}
              />
              <MetadataRow
                label="Max discharge rate"
                value={formatNumber(snapshot.ocppMetadata.maxDischargeRateWatts, 'W')}
              />
              <MetadataRow label="Set gradient" value={formatNumber(snapshot.ocppMetadata.setGradW, 'W/s')} />
            </Stack>
          ) : (
            <Text size="sm" c="dimmed">
              {isLoading ? 'Loading…' : 'No EVSE metadata has been recorded yet'}
            </Text>
          )}
        </Paper>

        <Paper withBorder p="md">
          <Group justify="space-between" mb="xs">
            <Text fw={600} size="sm">
              Health
            </Text>
            <Badge color="gray" variant="light" leftSection={<IconHeartbeat size={12} />}>
              Not implemented
            </Badge>
          </Group>
          <Text size="sm" c="dimmed">
            Device/connection health diagnostics will appear here once available.
          </Text>
        </Paper>
      </SimpleGrid>

      {chartData ? (
        <SimpleGrid cols={{ base: 1, lg: 2 }}>
          <TimeSeriesPanel
            title="Power"
            unit=" W"
            data={chartData.power}
            windowStartMs={chartData.windowStartMs}
            windowEndMs={chartData.windowEndMs}
            nowMs={chartData.nowMs}
            series={[
              { name: 'importLimitWatts', label: 'Import limit (sched.)', color: 'gray.5' },
              { name: 'exportLimitWatts', label: 'Export limit (sched.)', color: 'gray.6' },
              { name: 'loadLimitWatts', label: 'Load limit (sched.)', color: 'yellow.5' },
              { name: 'generationLimitWatts', label: 'Generation limit (sched.)', color: 'grape.5' },
              { name: 'storageTargetWatts', label: 'Storage target (sched.)', color: 'violet.5' },
              { name: 'importActivePowerWatts', label: 'Import power (actual)', color: 'blue.6' },
              { name: 'exportActivePowerWatts', label: 'Export power (actual)', color: 'teal.6' },
              ...satecSeries,
            ]}
          />

          <TimeSeriesPanel
            title="Reactive power"
            unit=" VAr"
            data={chartData.reactivePower}
            windowStartMs={chartData.windowStartMs}
            windowEndMs={chartData.windowEndMs}
            nowMs={chartData.nowMs}
            series={[
              { name: 'importReactivePowerVar', label: 'Import reactive power', color: 'blue.6' },
              { name: 'exportReactivePowerVar', label: 'Export reactive power', color: 'teal.6' },
              ...satecSeries,
            ]}
          />

          <TimeSeriesPanel
            title="Dynamic price"
            unit=" $/kWh"
            data={chartData.price}
            windowStartMs={chartData.windowStartMs}
            windowEndMs={chartData.windowEndMs}
            nowMs={chartData.nowMs}
            series={[{ name: 'priceKwh', label: 'Price', color: 'orange.6' }]}
          />

          <TimeSeriesPanel
            title="Frequency"
            unit=" Hz"
            data={chartData.frequency}
            windowStartMs={chartData.windowStartMs}
            windowEndMs={chartData.windowEndMs}
            nowMs={chartData.nowMs}
            series={[{ name: 'frequencyHz', label: 'Frequency', color: 'indigo.6' }, ...satecSeries]}
          />

          <TimeSeriesPanel
            title="Voltage"
            unit=" V"
            data={chartData.voltage}
            windowStartMs={chartData.windowStartMs}
            windowEndMs={chartData.windowEndMs}
            nowMs={chartData.nowMs}
            series={[{ name: 'voltageVolts', label: 'Voltage', color: 'cyan.6' }, ...satecSeries]}
            extraReferenceLines={[
              ...(snapshot?.ocppMetadata?.maxVoltageVolts != null
                ? [{ y: snapshot.ocppMetadata.maxVoltageVolts, label: 'Max' }]
                : []),
              ...(snapshot?.ocppMetadata?.minVoltageVolts != null
                ? [{ y: snapshot.ocppMetadata.minVoltageVolts, label: 'Min' }]
                : []),
            ]}
          />

          <TimeSeriesPanel
            title="State of charge"
            unit="%"
            data={chartData.soc}
            windowStartMs={chartData.windowStartMs}
            windowEndMs={chartData.windowEndMs}
            nowMs={chartData.nowMs}
            yAxisDomain={[0, 100]}
            series={[{ name: 'socPercent', label: 'SoC', color: 'lime.6' }]}
          />
        </SimpleGrid>
      ) : null}
    </Stack>
  )
}
