import { CheckCircle2, CircleSlash, HelpCircle, ShieldAlert, TriangleAlert } from "lucide-react";
import { Chip, VerdictBadge } from "@/components/status";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { GateResult } from "@/lib/api";
import { metricInfo } from "@/lib/metrics";
import { cn } from "@/lib/utils";

const BANNER = {
  PASS: { icon: CheckCircle2, cls: "border-success/40 bg-success/8 text-success", title: "Safe to release", text: "Every blocking rule passed." },
  WARN: { icon: TriangleAlert, cls: "border-warning/50 bg-warning/10 text-warning", title: "Release with caution", text: "No blocking rule failed, but at least one warning needs a look." },
  FAIL: { icon: ShieldAlert, cls: "border-danger/50 bg-danger/8 text-danger", title: "Release blocked", text: "At least one blocking rule failed. In CI this exits non-zero and the deploy job never runs." },
  INCONCLUSIVE: { icon: HelpCircle, cls: "border-warning/50 bg-warning/10 text-warning", title: "Inconclusive", text: "The sample is too small to tell whether a rule holds." },
  "N/A": { icon: CircleSlash, cls: "border-border bg-muted text-muted-foreground", title: "Not applicable", text: "" },
} as const;

export function VerdictBanner({ result }: { result: Pick<GateResult, "verdict" | "policy" | "target" | "rules" | "notes" | "comparability_blocks"> }) {
  const b = BANNER[result.verdict] ?? BANNER["N/A"];
  const Icon = b.icon;
  const failing = result.rules.filter((r) => r.verdict === "FAIL" && r.severity === "block");
  return (
    <div className={cn("flex items-start gap-3 rounded-xl border p-4", b.cls)}>
      <Icon className="mt-0.5 size-6 shrink-0" />
      <div className="min-w-0 text-foreground">
        <p className="text-base font-semibold">
          {b.title} <span className="font-normal text-muted-foreground">· {result.target} · policy “{result.policy}”</span>
        </p>
        <p className="text-sm text-muted-foreground">{b.text}</p>
        {failing.length > 0 && (
          <ul className="mt-2 grid gap-0.5 text-sm">
            {failing.map((r) => (
              <li key={r.rule}>
                <span className="font-medium">{r.metric ? metricInfo(r.metric).label : r.rule}</span>
                <span className="text-muted-foreground"> — {r.detail}</span>
              </li>
            ))}
          </ul>
        )}
        {result.comparability_blocks.map((m) => (
          <p key={m} className="mt-1 text-sm text-danger">
            Not comparable: {m}
          </p>
        ))}
      </div>
    </div>
  );
}

export function RuleTable({ rules }: { rules: GateResult["rules"] }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Rule</TableHead>
          <TableHead className="text-right">Baseline</TableHead>
          <TableHead className="text-right">Candidate</TableHead>
          <TableHead>Requirement</TableHead>
          <TableHead>Evidence</TableHead>
          <TableHead className="text-right">Verdict</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rules.map((r) => {
          const info = r.metric ? metricInfo(r.metric) : null;
          return (
            <TableRow key={r.rule}>
              <TableCell>
                <p className="font-medium">{info?.label ?? r.rule}</p>
                <p className="text-xs text-muted-foreground">
                  {r.metric ?? "critical cases"} {r.severity === "warn" && <Chip tone="muted" dot={false} className="ml-1">warn only</Chip>}
                </p>
              </TableCell>
              <TableCell className="tabular text-right text-muted-foreground">{info ? info.format(r.baseline) : "—"}</TableCell>
              <TableCell className="tabular text-right font-medium">{info ? info.format(r.candidate) : "—"}</TableCell>
              <TableCell className="tabular text-xs whitespace-normal text-muted-foreground">{r.threshold}</TableCell>
              <TableCell className="max-w-96 text-xs whitespace-normal text-muted-foreground">{r.detail}</TableCell>
              <TableCell className="text-right">
                <VerdictBadge verdict={r.verdict} />
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}
