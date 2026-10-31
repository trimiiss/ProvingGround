import Link from "next/link";
import { KindBadge } from "@/components/status";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { RunBrief } from "@/lib/api";
import { modelName, ms, num, pct, relTime, shortId, usd } from "@/lib/format";
import { primaryQuality, toneClass, toneFor } from "@/lib/metrics";

export function RunsTable({ runs, showAgent = true }: { runs: RunBrief[]; showAgent?: boolean }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Run</TableHead>
          {showAgent && <TableHead>Agent</TableHead>}
          <TableHead>Kind</TableHead>
          <TableHead>Model</TableHead>
          <TableHead className="text-right">Cases</TableHead>
          <TableHead className="text-right">Quality</TableHead>
          <TableHead className="text-right">Attacks won</TableHead>
          <TableHead className="text-right">p95</TableHead>
          <TableHead className="text-right">Cost</TableHead>
          <TableHead className="text-right">When</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {runs.map((r) => {
          const q = primaryQuality(r.metrics);
          const asr = r.metrics?.["safety.attack_success_rate"];
          return (
            <TableRow key={r.id}>
              <TableCell>
                <Link href={`/runs/${r.id}`} className="font-mono text-xs text-primary hover:underline" title={r.id}>
                  {shortId(r.id)}
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
                <KindBadge kind={r.kind} />
              </TableCell>
              <TableCell className="text-muted-foreground">{modelName(r.model)}</TableCell>
              <TableCell className="tabular text-right">{num(r.n_results)}</TableCell>
              <TableCell className={`tabular text-right ${q ? toneClass[toneFor(q.name, q.value)] : ""}`}>{q ? pct(q.value, 1) : "—"}</TableCell>
              <TableCell className={`tabular text-right ${toneClass[toneFor("safety.attack_success_rate", asr)]}`}>{asr === undefined ? "—" : pct(asr, 1)}</TableCell>
              <TableCell className="tabular text-right">{ms(r.metrics?.["perf.latency_p95_ms"])}</TableCell>
              <TableCell className="tabular text-right">{usd(r.cost_usd)}</TableCell>
              <TableCell className="text-right text-muted-foreground" title={r.created_at}>
                {relTime(r.created_at)}
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}
