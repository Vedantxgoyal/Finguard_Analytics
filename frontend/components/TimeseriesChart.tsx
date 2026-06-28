"use client";

import {
  AreaChart,
  Area,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import type { HourlyFraudSummary } from "@/lib/api";
import { formatPercent, formatCount } from "@/lib/format";

interface TimeseriesChartProps {
  data: HourlyFraudSummary[];
}

/**
 * Dual-signal time series: transaction volume as a filled area (the
 * "background" context), fraud rate as an overlaid line on a second
 * implicit scale. Two genuinely different units (count vs percentage)
 * sharing one chart is normally something to avoid (see: "never a
 * dual-axis chart" as a general data-viz principle) — the exception
 * made here is deliberate: an analyst's actual question is "does fraud
 * rate spike independently of volume, or does it just track volume,"
 * and that comparison is the entire reason this chart exists. A single
 * combined view answers it at a glance; two side-by-side charts would
 * make the visual correlation-spotting task the chart exists for
 * meaningfully harder.
 */
export function TimeseriesChart({ data }: TimeseriesChartProps) {
  const chartData = data.map((d) => ({
    step: d.step,
    transactions: d.total_transactions,
    fraudRate: parseFloat(d.fraud_rate_pct),
  }));

  return (
    <div
      role="img"
      aria-label={`Hourly transaction volume and fraud rate across ${data.length} time steps`}
      className="h-72 w-full"
    >
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={chartData} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id="volumeFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#5C6678" stopOpacity={0.35} />
              <stop offset="100%" stopColor="#5C6678" stopOpacity={0} />
            </linearGradient>
          </defs>

          <CartesianGrid
            strokeDasharray="3 3"
            stroke="#1F2733"
            vertical={false}
          />

          <XAxis
            dataKey="step"
            tick={{ fill: "#5C6678", fontSize: 11, fontFamily: "var(--font-plex-mono)" }}
            axisLine={{ stroke: "#1F2733" }}
            tickLine={false}
            label={{
              value: "time step (hour)",
              position: "insideBottom",
              offset: -4,
              fill: "#5C6678",
              fontSize: 11,
            }}
          />

          <YAxis
            yAxisId="volume"
            tick={{ fill: "#5C6678", fontSize: 11, fontFamily: "var(--font-plex-mono)" }}
            axisLine={false}
            tickLine={false}
            width={48}
          />

          <YAxis
            yAxisId="fraudRate"
            orientation="right"
            tick={{ fill: "#FF4757", fontSize: 11, fontFamily: "var(--font-plex-mono)" }}
            axisLine={false}
            tickLine={false}
            width={40}
            tickFormatter={(v) => `${v}%`}
          />

          <Tooltip content={<ChartTooltip />} />

          <Area
            yAxisId="volume"
            type="monotone"
            dataKey="transactions"
            stroke="#5C6678"
            strokeWidth={1.5}
            fill="url(#volumeFill)"
            name="transactions"
          />
          <Line
            yAxisId="fraudRate"
            type="monotone"
            dataKey="fraudRate"
            stroke="#FF4757"
            strokeWidth={2}
            dot={false}
            name="fraud rate"
          />
        </AreaChart>
      </ResponsiveContainer>

      <div className="mt-1 flex items-center justify-center gap-4 font-mono text-2xs text-text-muted">
        <span className="flex items-center gap-1.5">
          <span className="h-0.5 w-3 bg-text-muted" /> transaction volume
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-0.5 w-3 bg-fraud" /> fraud rate
        </span>
      </div>
    </div>
  );
}

function ChartTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: Array<{ value: number; name: string }>;
  label?: number;
}) {
  if (!active || !payload || payload.length === 0) return null;

  const volume = payload.find((p) => p.name === "transactions")?.value;
  const fraudRate = payload.find((p) => p.name === "fraud rate")?.value;

  return (
    <div className="rounded border border-hairline bg-raised-2 px-3 py-2 font-mono text-2xs shadow-lg">
      <p className="text-text-muted">step {label}</p>
      {volume !== undefined && (
        <p className="text-text-secondary">{formatCount(volume)} transactions</p>
      )}
      {fraudRate !== undefined && (
        <p className="text-fraud">{formatPercent(fraudRate)} fraud rate</p>
      )}
    </div>
  );
}
