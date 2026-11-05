import Link from "next/link";
import { MetricHelp } from "@/components/metric-help";
import { KindBadge, VerdictBadge } from "@/components/status";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { RunBrief } from "@/lib/api";
import { modelName, ms, num, pct, relTime, usd } from "@/lib/format";
import { metricInfo, primaryQuality, toneClass, toneFor } from "@/lib/metrics";

const ATTACKS = "safety.attack_success_rate";
const LATENCY = "perf.latency_p95_ms";

export function RunsTable({
  runs,
  showAgent = true,
  verdicts = {},
}: {
  runs: RunBrief[];
  showAgent?: boolean;
  /** run id -> gate verdict, for the runs that went through a gate */
  verdicts?: Record<string, string>;
}) {
  // Speed and cost are made up on the simulated models, so they only earn a column with a real one.
  const real = runs.some((r) => r.model && !r.model.startsWith("sim/"));
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>When</TableHead>
          {showAgent && <TableHead>Agent</TableHead>}
          <TableHead>Result</TableHead>
          <TableHead>Model</TableHead>
          <TableHead className="text-right">Cases</TableHead>
          <TableHead className="text-right">
            <span className="inline-flex items-center gap-1">
              Quality <MetricHelp text={metricInfo("quality.correctness").hint ?? ""} />
            </span>
          </TableHead>
          <TableHead className="text-right">
            <span className="inline-flex items-center gap-1">
              Attacks won <MetricHelp text={metricInfo(ATTACKS).hint ?? ""} />
            </span>
          </TableHead>
          {real && <TableHead className="text-right">p95</TableHead>}
          {real && <TableHead className="text-right">Cost</TableHead>}
        </TableRow>
      </TableHeader>
      <TableBody>
        {runs.map((r) => {
          const q = primaryQuality(r.metrics);
          const asr = r.metrics?.[ATTACKS];
          return (
            <TableRow key={r.id}>
              <TableCell>
                <Link href={`/runs/${r.id}`} className="text-primary hover:underline" title={`${r.id} · ${r.created_at}`}>
                  {relTime(r.created_at)}
                </Link>
              </TableCell>
              {showAgent && (
                <TableCell>
                  <Link href={`/agents/${r.agent}`} className="hover:underline">
                    {r.agent}
                  </Link>
                </TableCell>
              )}
              <TableCell>
                <span className="flex items-center gap-1.5">
                  {verdicts[r.id] && <VerdictBadge verdict={verdicts[r.id]} />}
                  {r.kind !== "eval" && <KindBadge kind={r.kind} />}
                  {r.kind === "eval" && !verdicts[r.id] && <span className="text-muted-foreground">—</span>}
                </span>
              </TableCell>
              <TableCell className="text-muted-foreground">{modelName(r.model)}</TableCell>
              <TableCell className="tabular text-right">{num(r.n_results)}</TableCell>
              <TableCell className={`tabular text-right ${q ? toneClass[toneFor(q.name, q.value)] : ""}`}>{q ? pct(q.value, 1) : "—"}</TableCell>
              <TableCell className={`tabular text-right ${toneClass[toneFor(ATTACKS, asr)]}`}>{asr === undefined ? "—" : pct(asr, 1)}</TableCell>
              {real && <TableCell className="tabular text-right">{ms(r.metrics?.[LATENCY])}</TableCell>}
              {real && <TableCell className="tabular text-right">{usd(r.cost_usd)}</TableCell>}
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}
