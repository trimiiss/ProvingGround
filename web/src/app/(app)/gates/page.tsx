import Link from "next/link";
import { Suspense } from "react";
import { FilterChips, first, withParams } from "@/components/filters";
import { Empty, Page, PageHeader, PageSkeleton, Section } from "@/components/page";
import { Chip, VerdictBadge } from "@/components/status";
import { Card } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { type GatePolicy, getGateEvaluations, getGatePolicies } from "@/lib/api";
import { dateTime, relTime, shortId } from "@/lib/format";
import { metricInfo } from "@/lib/metrics";

export const metadata = { title: "Quality gates" };

export default function GatesPage(props: PageProps<"/gates">) {
  return (
    <Suspense fallback={<PageSkeleton rows={2} />}>
      <Gates searchParams={props.searchParams} />
    </Suspense>
  );
}

function describeRule(r: NonNullable<GatePolicy["rules"]>[number]): string {
  if (r.critical_cases) return "every critical case must pass";
  const label = r.metric ? metricInfo(r.metric).label : (r.name ?? "rule");
  const fmt = (v: number) => (r.metric ? metricInfo(r.metric).format(v) : String(v));
  const parts: string[] = [];
  if (r.min != null) parts.push(`at least ${fmt(r.min)}`);
  if (r.max != null) parts.push(`at most ${fmt(r.max)}`);
  if (r.max_drop != null) parts.push(`no more than ${typeof r.max_drop === "string" ? r.max_drop : fmt(r.max_drop)} below baseline`);
  if (r.max_increase != null) parts.push(`no more than ${typeof r.max_increase === "string" ? r.max_increase : fmt(r.max_increase)} above baseline`);
  return `${label}: ${parts.join(", ")}`;
}

async function Gates({ searchParams }: { searchParams: PageProps<"/gates">["searchParams"] }) {
  const sp = await searchParams;
  const agent = first(sp.agent) ?? "all";
  const [policies, evaluations] = await Promise.all([getGatePolicies(), getGateEvaluations({ agent: agent === "all" ? undefined : agent, limit: 60 })]);
  const agents = [...new Set(evaluations.map((e) => e.agent))].sort();

  return (
    <Page>
      <PageHeader title="Quality gates" description="A gate compares a change against the version in production and decides if it may ship. In CI a failed gate stops the deploy; here is the history of those decisions and the rules behind them." />

      <Section title="Policies">
        <div className="grid gap-3 lg:grid-cols-2">
          {Object.entries(policies).map(([name, p]) => (
            <Card key={name} className="gap-3 p-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="font-medium">{name}</p>
                  <p className="text-xs text-muted-foreground">{p.description || "No description"}</p>
                </div>
                <div className="flex flex-col items-end gap-1 text-[11px] text-muted-foreground">
                  <span>baseline <span className="font-mono">{p.baseline ?? "none"}</span></span>
                  <span>inconclusive → {p.on_inconclusive}</span>
                </div>
              </div>
              <ul className="grid gap-1 text-sm">
                {(p.rules ?? []).map((r, i) => (
                  <li key={i} className="flex items-start gap-2">
                    <Chip tone={r.severity === "block" ? "danger" : "warning"} dot={false} className="mt-0.5 shrink-0">
                      {r.severity === "block" ? "blocks" : "warns"}
                    </Chip>
                    <span className="text-muted-foreground">{describeRule(r)}</span>
                  </li>
                ))}
              </ul>
            </Card>
          ))}
          {Object.keys(policies).length === 0 && <Empty title="No gate policies configured" />}
        </div>
      </Section>

      <Section
        title="Decisions"
        actions={<FilterChips current={agent} options={[{ value: "all" }, ...agents.map((a) => ({ value: a }))]} href={(v) => withParams("/gates", {}, { agent: v })} />}
      >
        {evaluations.length === 0 ? (
          <Empty title="No gate decisions yet" />
        ) : (
          <Card className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>When</TableHead>
                  <TableHead>Agent</TableHead>
                  <TableHead>Policy</TableHead>
                  <TableHead>Candidate run</TableHead>
                  <TableHead>Baseline run</TableHead>
                  <TableHead className="text-right">Verdict</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {evaluations.map((e) => (
                  <TableRow key={e.id}>
                    <TableCell>
                      <Link href={`/gates/${e.id}`} className="text-primary hover:underline" title={e.created_at}>
                        {dateTime(e.created_at)}
                      </Link>
                      <p className="text-[11px] text-muted-foreground">{relTime(e.created_at)}</p>
                    </TableCell>
                    <TableCell>
                      <Link href={`/agents/${e.agent}`} className="hover:underline">
                        {e.agent}
                      </Link>
                    </TableCell>
                    <TableCell>{e.policy}</TableCell>
                    <TableCell className="font-mono text-xs">
                      <Link href={`/runs/${e.candidate_run_id}`} className="hover:underline">
                        {shortId(e.candidate_run_id)}
                      </Link>
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {e.baseline_run_id ? (
                        <Link href={`/runs/${e.baseline_run_id}`} className="hover:underline">
                          {shortId(e.baseline_run_id)}
                        </Link>
                      ) : (
                        "—"
                      )}
                    </TableCell>
                    <TableCell className="text-right">
                      <VerdictBadge verdict={e.verdict} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Card>
        )}
      </Section>
    </Page>
  );
}
