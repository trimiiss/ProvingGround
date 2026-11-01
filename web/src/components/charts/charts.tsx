"use client";

import {
  Area,
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";
import { ms, pct, usd } from "@/lib/format";

type Fmt = "pct" | "ms" | "usd" | "num";
const FORMATS: Record<Fmt, (v: number) => string> = {
  pct: (v) => pct(v, 0),
  ms: (v) => ms(v),
  usd: (v) => usd(v),
  num: (v) => v.toLocaleString("en-US", { maximumFractionDigits: 2 }),
};

/** Compact tick labels: axes have little room, tooltips carry the precise values. */
const TICKS: Record<Fmt, (v: number) => string> = {
  pct: (v) => pct(v, 0),
  ms: (v) => (v >= 1000 ? `${(v / 1000).toFixed(v >= 10000 ? 0 : 1)}s` : `${Math.round(v)}ms`),
  usd: (v) => (v === 0 ? "$0" : `$${Number(v.toPrecision(2))}`),
  num: (v) => v.toLocaleString("en-US", { notation: "compact", maximumFractionDigits: 1 }),
};

/** Round ticks from 0 to just above `max`, in steps of 1, 2, 2.5 or 5 × 10^k. */
function niceTicks(max: number, count = 4): number[] {
  if (!(max > 0)) return [0, 1];
  const raw = max / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw * (1 - 1e-9)) ?? 10 * mag;
  const n = Math.ceil(max / step - 1e-9);
  return Array.from({ length: n + 1 }, (_, i) => Number((i * step).toPrecision(12)));
}

/** Percent axes stay within 0–100% (rounded out to the nearest 5%). */
const PCT_DOMAIN: [(min: number) => number, (max: number) => number] = [
  (min) => Math.max(0, Math.floor(min * 20) / 20),
  (max) => Math.min(1, Math.ceil(max * 20) / 20),
];

const AXIS = { fontSize: 11, fill: "var(--muted-foreground)" } as const;
const GRID = { stroke: "var(--border)", strokeDasharray: "3 3", vertical: false } as const;

function dayLabel(iso: string) {
  return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}
function hourLabel(iso: string) {
  return new Date(iso).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false });
}

interface TipProps {
  active?: boolean;
  payload?: { name?: string; value?: number | string; color?: string; payload?: Record<string, unknown> }[];
  label?: string | number;
  fmt: Record<string, (v: number) => string>;
  xFormat: (v: string) => string;
  extra?: (row: Record<string, unknown>) => string | null;
}

function Tip({ active, payload, label, fmt, xFormat, extra }: TipProps) {
  if (!active || !payload?.length) return null;
  const row = payload[0].payload ?? {};
  const note = extra?.(row);
  return (
    <div className="rounded-lg border bg-popover px-3 py-2 text-xs shadow-md">
      <p className="mb-1 font-medium">{typeof label === "string" ? xFormat(label) : label}</p>
      {payload.map((p) => (
        <p key={p.name} className="flex items-center gap-2 tabular">
          <span className="size-2 rounded-full" style={{ background: p.color }} />
          <span className="text-muted-foreground">{p.name}</span>
          <span className="ml-auto pl-3 font-medium">
            {typeof p.value === "number" ? (fmt[p.name ?? ""] ?? FORMATS.num)(p.value) : "—"}
          </span>
        </p>
      ))}
      {note && <p className="mt-1 text-muted-foreground">{note}</p>}
    </div>
  );
}

export interface TrendPointData {
  t: string;
  value: number | null;
  model?: string | null;
}

/** One metric over time, with a vertical marker wherever the model changed. */
export function TrendChart({
  data,
  format = "pct",
  height = 220,
  name = "value",
  color = "var(--chart-1)",
  domain,
}: {
  data: TrendPointData[];
  format?: Fmt;
  height?: number;
  name?: string;
  color?: string;
  domain?: [number | "auto", number | "auto"];
}) {
  const changes: { t: string; label: string; end: boolean }[] = [];
  data.forEach((d, i) => {
    if (i > 0 && d.model && data[i - 1].model && d.model !== data[i - 1].model) {
      // Near the right edge the label goes on the left of its line so it is not clipped.
      changes.push({ t: d.t, label: `→ ${d.model.split("/").pop()}`, end: i > data.length * 0.75 });
    }
  });
  // Labels sit just right of their line, on alternating rows so nearby changes stay readable.
  const marker = (text: string, row: number, end: boolean) =>
    function MarkerLabel({ viewBox }: { viewBox?: { x?: number; y?: number } }) {
      return (
        <text x={(viewBox?.x ?? 0) + (end ? -4 : 4)} y={(viewBox?.y ?? 0) + 10 + row * 12} textAnchor={end ? "end" : "start"} fontSize={10} fill="var(--chart-4)">
          {text}
        </text>
      );
    };
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 14, right: 12, bottom: 0, left: 0 }}>
        <CartesianGrid {...GRID} />
        <XAxis dataKey="t" tickFormatter={dayLabel} tick={AXIS} tickLine={false} axisLine={false} minTickGap={36} />
        <YAxis tickFormatter={TICKS[format]} tick={AXIS} tickLine={false} axisLine={false} width={48} domain={domain ?? (format === "pct" ? PCT_DOMAIN : ["auto", "auto"])} />
        <Tooltip
          cursor={{ stroke: "var(--border)" }}
          content={<Tip fmt={{ [name]: FORMATS[format] }} xFormat={dayLabel} extra={(r) => (r.model ? `model ${String(r.model).split("/").pop()}` : null)} />}
        />
        {changes.map((c, i) => (
          <ReferenceLine key={c.t} x={c.t} stroke="var(--chart-4)" strokeDasharray="4 3" label={marker(c.label, i % 2, c.end)} />
        ))}
        <Line type="monotone" dataKey="value" name={name} stroke={color} strokeWidth={2} dot={false} activeDot={{ r: 4 }} connectNulls isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}

export interface TrafficPoint {
  ts: string;
  requests: number;
  errors: number;
  p50_ms: number | null;
  p95_ms: number | null;
  cost_usd: number;
  tokens: number;
}

/** Requests per bucket (errors stacked in red). */
export function RequestsChart({ data, height = 200 }: { data: TrafficPoint[]; height?: number }) {
  const rows = data.map((d) => ({ ...d, ok: d.requests - d.errors }));
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={rows} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
        <CartesianGrid {...GRID} />
        <XAxis dataKey="ts" tickFormatter={hourLabel} tick={AXIS} tickLine={false} axisLine={false} minTickGap={36} />
        <YAxis tickFormatter={TICKS.num} tick={AXIS} tickLine={false} axisLine={false} width={40} allowDecimals={false} />
        <Tooltip cursor={{ fill: "var(--muted)", opacity: 0.4 }} content={<Tip fmt={{}} xFormat={hourLabel} />} />
        <Bar dataKey="ok" name="requests" stackId="a" fill="var(--chart-1)" radius={[0, 0, 0, 0]} isAnimationActive={false} />
        <Bar dataKey="errors" name="errors" stackId="a" fill="var(--danger)" radius={[2, 2, 0, 0]} isAnimationActive={false} />
      </ComposedChart>
    </ResponsiveContainer>
  );
}

export function LatencyChart({ data, height = 200 }: { data: TrafficPoint[]; height?: number }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
        <CartesianGrid {...GRID} />
        <XAxis dataKey="ts" tickFormatter={hourLabel} tick={AXIS} tickLine={false} axisLine={false} minTickGap={36} />
        <YAxis tickFormatter={TICKS.ms} tick={AXIS} tickLine={false} axisLine={false} width={48} />
        <Tooltip cursor={{ stroke: "var(--border)" }} content={<Tip fmt={{ p50: FORMATS.ms, p95: FORMATS.ms }} xFormat={hourLabel} />} />
        <Legend iconType="plainline" wrapperStyle={{ fontSize: 11 }} />
        <Line type="monotone" dataKey="p50_ms" name="p50" stroke="var(--chart-2)" strokeWidth={2} dot={false} connectNulls isAnimationActive={false} />
        <Line type="monotone" dataKey="p95_ms" name="p95" stroke="var(--chart-4)" strokeWidth={2} dot={false} connectNulls isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function CostChart({ data, height = 200 }: { data: TrafficPoint[]; height?: number }) {
  const ticks = niceTicks(Math.max(0, ...data.map((d) => d.cost_usd)));
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
        <CartesianGrid {...GRID} />
        <XAxis dataKey="ts" tickFormatter={hourLabel} tick={AXIS} tickLine={false} axisLine={false} minTickGap={36} />
        <YAxis tickFormatter={TICKS.usd} ticks={ticks} domain={[0, ticks[ticks.length - 1]]} tick={AXIS} tickLine={false} axisLine={false} width={52} />
        <Tooltip cursor={{ stroke: "var(--border)" }} content={<Tip fmt={{ spend: FORMATS.usd }} xFormat={hourLabel} />} />
        <Area type="monotone" dataKey="cost_usd" name="spend" stroke="var(--chart-3)" fill="var(--chart-3)" fillOpacity={0.18} strokeWidth={2} isAnimationActive={false} />
      </ComposedChart>
    </ResponsiveContainer>
  );
}

export interface ParetoPoint {
  model: string;
  quality: number;
  cost: number;
  latency: number | null;
  pareto: boolean;
}

/** Quality vs cost per 1k requests. The Pareto front (nothing is both better and cheaper) is highlighted. */
export function ParetoChart({ data, height = 340 }: { data: ParetoPoint[]; height?: number }) {
  const front = data.filter((d) => d.pareto).sort((a, b) => a.cost - b.cost);
  const others = data.filter((d) => !d.pareto);
  const shape = (color: string, big: boolean) =>
    function Dot(props: { cx?: number; cy?: number; payload?: ParetoPoint }) {
      const { cx = 0, cy = 0, payload } = props;
      return (
        <g>
          <circle cx={cx} cy={cy} r={big ? 7 : 5} fill={color} fillOpacity={big ? 0.9 : 0.55} stroke="var(--background)" strokeWidth={2} />
          <text x={cx + 11} y={cy + 4} fontSize={11} fill="var(--foreground)">
            {payload?.model.split("/").pop()}
          </text>
        </g>
      );
    };
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ScatterChart margin={{ top: 16, right: 80, bottom: 24, left: 8 }}>
        <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
        <XAxis type="number" dataKey="cost" name="cost" scale="log" domain={["auto", "auto"]} tickFormatter={(v: number) => usd(v, v < 1 ? 2 : 0)} tick={AXIS} tickLine={false} label={{ value: "cost per 1k requests (log scale)", position: "insideBottom", offset: -12, fontSize: 11, fill: "var(--muted-foreground)" }} />
        <YAxis type="number" dataKey="quality" name="quality" tickFormatter={(v: number) => pct(v, 0)} tick={AXIS} tickLine={false} domain={["auto", "auto"]} width={48} />
        <ZAxis range={[80, 80]} />
        <Tooltip
          cursor={{ strokeDasharray: "3 3" }}
          content={({ active, payload }) =>
            active && payload?.length ? (
              <div className="rounded-lg border bg-popover px-3 py-2 text-xs shadow-md">
                <p className="font-medium">{(payload[0].payload as ParetoPoint).model}</p>
                <p className="text-muted-foreground tabular">
                  quality {pct((payload[0].payload as ParetoPoint).quality, 1)} · {usd((payload[0].payload as ParetoPoint).cost)} / 1k
                  {(payload[0].payload as ParetoPoint).latency ? ` · p95 ${ms((payload[0].payload as ParetoPoint).latency)}` : ""}
                </p>
              </div>
            ) : null
          }
        />
        <Scatter data={front} line={{ stroke: "var(--chart-1)", strokeWidth: 1.5, strokeDasharray: "5 4" }} shape={shape("var(--chart-1)", true)} isAnimationActive={false} />
        <Scatter data={others} shape={shape("var(--chart-6)", false)} isAnimationActive={false} />
      </ScatterChart>
    </ResponsiveContainer>
  );
}
