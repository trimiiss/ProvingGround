import { cn } from "@/lib/utils";

/** A tiny inline trend line (server-rendered SVG, no JS). */
export function Sparkline({
  values,
  className,
  width = 96,
  height = 28,
}: {
  values: number[];
  className?: string;
  width?: number;
  height?: number;
}) {
  if (values.length < 2) return <div style={{ width, height }} aria-hidden />;
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const span = hi - lo || 1;
  const pad = 2;
  const pts = values.map((v, i) => [pad + (i / (values.length - 1)) * (width - pad * 2), pad + (1 - (v - lo) / span) * (height - pad * 2)]);
  const d = pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`).join(" ");
  const last = pts[pts.length - 1];
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} className={cn("overflow-visible", className)} aria-hidden>
      <path d={d} fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={last[0]} cy={last[1]} r="2.2" fill="currentColor" />
    </svg>
  );
}

/** Horizontal bar with a label and value, used for breakdowns. */
export function BarRow({
  label,
  value,
  max = 1,
  display,
  tone = "primary",
  sub,
}: {
  label: React.ReactNode;
  value: number;
  max?: number;
  display: string;
  tone?: "primary" | "danger" | "success" | "warning";
  sub?: React.ReactNode;
}) {
  const color = { primary: "bg-chart-1", danger: "bg-danger", success: "bg-success", warning: "bg-warning" }[tone];
  return (
    <div className="grid grid-cols-[minmax(0,10rem)_1fr_auto] items-center gap-3 text-sm">
      <span className="truncate" title={typeof label === "string" ? label : undefined}>
        {label}
      </span>
      <span className="h-2 overflow-hidden rounded-full bg-muted">
        <span className={cn("block h-full rounded-full", color)} style={{ width: `${Math.max(1, Math.min(100, (value / (max || 1)) * 100))}%` }} />
      </span>
      <span className="tabular w-24 text-right text-xs text-muted-foreground">
        <span className="font-medium text-foreground">{display}</span>
        {sub && <> {sub}</>}
      </span>
    </div>
  );
}

/** Red intensity heat cell for an attack-success rate (0..1). */
export function heat(rate: number | null | undefined): React.CSSProperties {
  if (rate === null || rate === undefined) return {};
  return { backgroundColor: `color-mix(in oklch, var(--danger) ${Math.round(8 + Math.min(1, rate) * 72)}%, transparent)` };
}
