import Link from "next/link";
import { Suspense } from "react";
import { RequestsChart, type TrafficPoint } from "@/components/charts/charts";
import { Sparkline } from "@/components/charts/visuals";
import { LaunchRun } from "@/components/launch-run";
import { MetricTile } from "@/components/metric-tile";
import { Empty, Page, PageHeader, PageSkeleton, Section } from "@/components/page";
import { HealthBadge, VerdictBadge } from "@/components/status";
import { Card } from "@/components/ui/card";
import { type AgentHealth, getConfig, getFleetTraffic, getOverview } from "@/lib/api";
import { modelName, ms, num, pct, relTime, usd } from "@/lib/format";
import { metricInfo, toneClass, toneFor } from "@/lib/metrics";
import { cn } from "@/lib/utils";

export const metadata = { title: "Fleet" };

export default function FleetPage(props: PageProps<"/">) {
  return (
    <Suspense fallback={<PageSkeleton rows={2} />}>
      <Fleet searchParams={props.searchParams} />
    </Suspense>
  );
}

const STATUS_ORDER = { failing: 0, warning: 1, unknown: 2, healthy: 3 } as const;

async function Fleet({ searchParams }: { searchParams: PageProps<"/">["searchParams"] }) {
  const sp = await searchParams;
  const status = typeof sp.status === "string" ? sp.status : "all";
  const team = typeof sp.team === "string" ? sp.team : "all";
  const [overview, traffic, config] = await Promise.all([getOverview(), getFleetTraffic(24, 60), getConfig()]);
  const t = overview.totals;
  const teams = [...new Set(overview.agents.map((a) => a.agent.team).filter(Boolean))].sort();
  const agents = overview.agents
    .filter((a) => (status === "all" || a.status === status) && (team === "all" || a.agent.team === team))
    .sort((a, b) => STATUS_ORDER[a.status] - STATUS_ORDER[b.status] || a.agent.slug.localeCompare(b.agent.slug));

  const points: TrafficPoint[] = traffic.map((b) => ({
    ts: b.ts, requests: b.requests, errors: b.errors, p50_ms: b.p50_ms ?? null, p95_ms: b.p95_ms ?? null, cost_usd: b.cost_usd, tokens: b.tokens_in + b.tokens_out,
  }));
  const href = (next: Record<string, string>) => {
    const q = new URLSearchParams({ ...(status !== "all" ? { status } : {}), ...(team !== "all" ? { team } : {}), ...next });
    for (const [k, v] of [...q]) if (v === "all") q.delete(k);
    return q.size ? `/?${q}` : "/";
  };

  return (
    <Page>
      <PageHeader
        title="Fleet"
        description="Quality, safety, cost and reliability of every AI agent, from the latest evaluation and live traffic."
        actions={<LaunchRun agents={overview.agents.map((a) => ({ slug: a.agent.slug, name: a.agent.name, model: a.agent.model ?? null, suites: a.agent.suites ?? [] }))} gates={config.gates} />}
      />

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <MetricTile label="Agents" value={t.agents} sub={<><span className="text-success">{t.healthy} healthy</span> · <span className="text-warning">{t.warning} warn</span> · <span className="text-danger">{t.failing} failing</span></>} />
        <MetricTile label="Requests · 24h" value={num(t.requests_24h)} />
        <MetricTile label="Error rate · 24h" value={pct(t.error_rate_24h, 2)} tone={t.error_rate_24h > 0.05 ? "bad" : t.error_rate_24h > 0.02 ? "warn" : "good"} />
        <MetricTile label="Spend · 24h" value={usd(t.cost_24h, 2)} />
        <MetricTile label="Evaluations · 7d" value={num(t.runs_7d)} />
        <MetricTile label="Blocked releases · 7d" value={t.gate_failures_7d} tone={t.gate_failures_7d > 0 ? "warn" : "good"} sub="gate failures" />
      </div>

      <div className="grid gap-4 xl:grid-cols-[1fr_340px]">
        <Section title="Production traffic" description="Requests per hour across the fleet, errors in red">
          <Card className="p-4">{points.length ? <RequestsChart data={points} /> : <Empty title="No live traffic yet">Start the traffic generator: <code>docker compose --profile traffic up -d traffic</code></Empty>}</Card>
        </Section>
        <Section title="Recent release gates">
          <Card className="divide-y p-0">
            {overview.recent_gates.length === 0 && <p className="p-4 text-sm text-muted-foreground">No gate evaluations yet.</p>}
            {overview.recent_gates.slice(0, 6).map((g) => (
              <Link key={g.id} href={`/gates/${g.id}`} className="flex items-center justify-between gap-3 px-4 py-2.5 text-sm transition-colors hover:bg-muted/50">
                <span className="min-w-0">
                  <span className="block truncate font-medium">{g.agent}</span>
                  <span className="text-xs text-muted-foreground">{g.policy} · {relTime(g.created_at)}</span>
                </span>
                <VerdictBadge verdict={g.verdict} />
              </Link>
            ))}
          </Card>
        </Section>
      </div>

      <Section
        title={`Agents (${agents.length})`}
        actions={
          <div className="flex flex-wrap items-center gap-1.5 text-xs">
            {["all", "failing", "warning", "healthy"].map((s) => (
              <Link key={s} href={href({ status: s })} className={cn("rounded-full border px-2.5 py-1 capitalize transition-colors hover:bg-muted", status === s && "border-primary bg-primary/10 font-medium text-primary")}>
                {s}
              </Link>
            ))}
            <span className="mx-1 h-4 w-px bg-border" />
            {["all", ...teams].map((tm) => (
              <Link key={tm} href={href({ team: tm })} className={cn("rounded-full border px-2.5 py-1 transition-colors hover:bg-muted", team === tm && "border-primary bg-primary/10 font-medium text-primary")}>
                {tm}
              </Link>
            ))}
          </div>
        }
      >
        {overview.agents.length === 0 ? (
          <Empty title="No agents yet">
            Drop a test folder on the{" "}
            <Link href="/upload" className="font-medium text-primary hover:underline">
              Upload agent
            </Link>{" "}
            page. To try it, use <code>examples/upload-me</code> from the repository.
          </Empty>
        ) : agents.length === 0 ? <Empty title="No agents match these filters" /> : (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
            {agents.map((a) => <AgentCard key={a.agent.slug} a={a} />)}
          </div>
        )}
      </Section>
    </Page>
  );
}

function AgentCard({ a }: { a: AgentHealth }) {
  const run = a.latest_run;
  const q = a.quality_metric ? metricInfo(a.quality_metric) : null;
  const asrTone = toneFor("safety.attack_success_rate", a.asr);
  return (
    <Link href={`/agents/${a.agent.slug}`} className="group focus-visible:outline-none">
      <Card className="h-full gap-3 p-4 transition-shadow group-hover:shadow-md group-focus-visible:ring-2 group-focus-visible:ring-ring">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="truncate font-medium">{a.agent.name}</p>
            <p className="truncate text-xs text-muted-foreground">{a.agent.slug} · {modelName(a.agent.model)} · {a.agent.team}</p>
          </div>
          <HealthBadge status={a.status} />
        </div>

        <div className="flex items-end justify-between gap-3">
          <div>
            <p className="text-xs text-muted-foreground">{q?.label ?? "Quality"}</p>
            <p className="tabular text-2xl font-semibold tracking-tight">{a.quality === null || a.quality === undefined ? "—" : pct(a.quality, 1)}</p>
          </div>
          <Sparkline values={a.trend ?? []} className="text-chart-1" />
        </div>

        <dl className="grid grid-cols-3 gap-2 text-xs">
          <div>
            <dt className="text-muted-foreground">Attacks won</dt>
            <dd className={cn("tabular font-medium", toneClass[asrTone])}>{a.asr === null || a.asr === undefined ? "—" : pct(a.asr, 1)}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">p95 (eval)</dt>
            <dd className="tabular font-medium">{ms(run?.metrics?.["perf.latency_p95_ms"])}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Requests 24h</dt>
            <dd className="tabular font-medium">{num(a.traffic?.requests)}</dd>
          </div>
        </dl>

        <div className="mt-auto flex items-center justify-between border-t pt-2.5 text-xs text-muted-foreground">
          {(a.reasons ?? []).length > 0 && a.status !== "healthy" ? (
            <span className={cn("truncate", a.status === "failing" ? "text-danger" : "text-warning")} title={(a.reasons ?? []).join("; ")}>{(a.reasons ?? [])[0]}</span>
          ) : (
            <span>{run ? `evaluated ${relTime(run.created_at)}` : "never evaluated"}</span>
          )}
          {a.gate && <VerdictBadge verdict={a.gate.verdict} />}
        </div>
      </Card>
    </Link>
  );
}
