import { Suspense } from "react";
import { type ParetoPoint, ParetoChart } from "@/components/charts/charts";
import { Empty, Page, PageHeader, PageSkeleton, Section } from "@/components/page";
import { Chip } from "@/components/status";
import { Card } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { getModels } from "@/lib/api";
import { ms, num, pct, usd } from "@/lib/format";
import { toneClass, toneFor } from "@/lib/metrics";

export const metadata = { title: "Models" };

export default function ModelsPage() {
  return (
    <Suspense fallback={<PageSkeleton rows={2} />}>
      <Models />
    </Suspense>
  );
}

async function Models() {
  const models = (await getModels(30)).sort((a, b) => (b.metrics["quality.correctness"] ?? 0) - (a.metrics["quality.correctness"] ?? 0));
  const points: ParetoPoint[] = models
    .filter((m) => m.metrics["quality.correctness"] !== undefined && m.cost_per_1k_usd)
    .map((m) => ({ model: m.model, quality: m.metrics["quality.correctness"], cost: m.cost_per_1k_usd!, latency: m.metrics["perf.latency_p95_ms"] ?? null, pareto: m.on_pareto_front }));
  const cell = (name: string, v: number | undefined) => <TableCell className={`tabular text-right ${toneClass[toneFor(name, v)]}`}>{v === undefined ? "—" : pct(v, 1)}</TableCell>;

  return (
    <Page>
      <PageHeader title="Models" description="Every model in use across the fleet, from the last 30 days of evaluations and live traffic. The ones on the dashed line are the best trade-offs: nothing else is both better and cheaper." />
      {models.length === 0 ? (
        <Empty title="No model data yet" />
      ) : (
        <>
          <Section title="Quality vs cost" description="Correctness against cost per 1,000 requests (log scale).">
            <Card className="p-4">
              <ParetoChart data={points} />
            </Card>
          </Section>
          <Section title="Leaderboard" description="Averages over all runs of the agents using each model. Agents differ in difficulty, so compare models on the same agent (the Compare page) before acting on a small gap.">
            <Card className="p-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Model</TableHead>
                    <TableHead className="text-right">Agents</TableHead>
                    <TableHead className="text-right">Runs</TableHead>
                    <TableHead className="text-right">Correctness</TableHead>
                    <TableHead className="text-right">Faithfulness</TableHead>
                    <TableHead className="text-right">Hallucination</TableHead>
                    <TableHead className="text-right">Attacks won</TableHead>
                    <TableHead className="text-right">False refusals</TableHead>
                    <TableHead className="text-right">p95 (eval)</TableHead>
                    <TableHead className="text-right">Cost / 1k</TableHead>
                    <TableHead className="text-right">Live requests</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {models.map((m) => (
                    <TableRow key={m.model}>
                      <TableCell>
                        <span className="font-medium">{m.model}</span>
                        {m.on_pareto_front && <Chip tone="info" dot={false} className="ml-2">best trade-off</Chip>}
                        <p className="max-w-64 truncate text-xs text-muted-foreground" title={m.agents.join(", ")}>
                          {m.agents.join(", ")}
                        </p>
                      </TableCell>
                      <TableCell className="tabular text-right">{m.agents.length}</TableCell>
                      <TableCell className="tabular text-right">{num(m.runs)}</TableCell>
                      {cell("quality.correctness", m.metrics["quality.correctness"])}
                      {cell("factuality.faithfulness", m.metrics["factuality.faithfulness"])}
                      {cell("factuality.hallucination_rate", m.metrics["factuality.hallucination_rate"])}
                      {cell("safety.attack_success_rate", m.metrics["safety.attack_success_rate"])}
                      {cell("safety.false_refusal_rate", m.metrics["safety.false_refusal_rate"])}
                      <TableCell className="tabular text-right">{ms(m.metrics["perf.latency_p95_ms"])}</TableCell>
                      <TableCell className="tabular text-right">{usd(m.cost_per_1k_usd)}</TableCell>
                      <TableCell className="tabular text-right">
                        {num(m.traffic_requests)}
                        {m.traffic_p95_ms ? <span className="block text-xs text-muted-foreground">p95 {ms(m.traffic_p95_ms)}</span> : null}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Card>
          </Section>
        </>
      )}
    </Page>
  );
}
