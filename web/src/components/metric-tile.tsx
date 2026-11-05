import { ArrowDownRight, ArrowUpRight } from "lucide-react";
import { MetricHelp } from "@/components/metric-help";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

export function MetricTile({
  label,
  value,
  sub,
  tone,
  help,
  className,
}: {
  label: React.ReactNode;
  value: React.ReactNode;
  sub?: React.ReactNode;
  help?: string;
  tone?: "good" | "warn" | "bad" | "neutral";
  className?: string;
}) {
  const color = { good: "text-success", warn: "text-warning", bad: "text-danger", neutral: "" }[tone ?? "neutral"];
  return (
    <Card className={cn("gap-1 px-4 py-3.5", className)}>
      <p className="flex items-center gap-1 text-xs text-muted-foreground">
        {label}
        {help && <MetricHelp text={help} />}
      </p>
      <p className={cn("tabular text-2xl font-semibold tracking-tight", color)}>{value}</p>
      {sub && <p className="text-xs text-muted-foreground">{sub}</p>}
    </Card>
  );
}

/** A signed change with colour that reflects whether it is good (direction-aware). */
export function Delta({
  delta,
  format,
  lowerIsBetter,
  className,
}: {
  delta: number | null | undefined;
  format: (v: number) => string;
  lowerIsBetter: boolean;
  className?: string;
}) {
  if (delta === null || delta === undefined) return <span className="text-muted-foreground">—</span>;
  const flat = Math.abs(delta) < 1e-9;
  const better = lowerIsBetter ? delta < 0 : delta > 0;
  const Icon = delta > 0 ? ArrowUpRight : ArrowDownRight;
  return (
    <span className={cn("tabular inline-flex items-center gap-0.5 font-medium", flat ? "text-muted-foreground" : better ? "text-success" : "text-danger", className)}>
      {!flat && <Icon className="size-3.5" />}
      {delta > 0 ? "+" : delta < 0 ? "−" : ""}
      {format(Math.abs(delta))}
    </span>
  );
}
