import { ExternalLink } from "lucide-react";
import Link from "next/link";
import { Suspense } from "react";
import { CostChart, LatencyChart, RequestsChart, type TrafficPoint, TrendChart } from "@/components/charts/charts";
import { BarRow } from "@/components/charts/visuals";
import { LaunchRun } from "@/components/launch-run";
import { MetricTile } from "@/components/metric-tile";
import { Empty, Page, PageHeader, PageSkeleton, Section } from "@/components/page";
import { RunsTable } from "@/components/runs-table";
import { HealthBadge, VerdictBadge } from "@/components/status";
import { Card } from "@/components/ui/card";
import {
  getAgent,
  getAgentTraffic,
  getAgentTrend,
  getConfig,
  getGateEvaluations,
  getRedteam,
  getRuns,
  orNotFound,
  orNull,
} from "@/lib/api";
import { modelName, ms, num, pct, relTime, usd } from "@/lib/format";
import { HEADLINE, metricInfo, TREND_METRICS, toneFor } from "@/lib/metrics";

const ONLINE_METRICS = ["factuality.faithfulness", "factuality.hallucination_rate", "safety.canary_leak_rate", "safety.pii_leak_rate", "quality.refusal_rate"];

export async function generateMetadata(props: PageProps<"/agents/[slug]">) {
  return { title: (await props.params).slug };
}

export default function AgentPage(props: PageProps<"/agents/[slug]">) {
  return (
    <Suspense fallback={<PageSkeleton rows={3} />}>
      <AgentDetail params={props.params} />
    </Suspense>
  );
}

async function AgentDetail({ params }: { params: PageProps<"/agents/[slug]">["params"] }) {
  const { slug } = await params;
  const a = await orNotFound(getAgent(slug));
  const [config, runs, gates, traffic, redteamRuns, ...trends] = await Promise.all([
    getConfig(),
    getRuns({ agent: slug, limit: 8 }),
    getGateEvaluations({ agent: slug, limit: 6 }),
    getAgentTraffic(slug, 24, 60),
    getRuns({ agent: slug, kind: "redteam", limit: 1 }),
    ...TREND_METRICS.map((t) => getAgentTrend(slug, t.metric, 30)),
  ]);
  const redteam = redteamRuns.items[0] ? await orNull(getRedteam(redteamRuns.items[0].id)) : null;

  const metrics = a.latest_run?.metrics ?? {};
  const points: TrafficPoint[] = traffic.map((b) => ({
    ts: b.ts, requests: b.requests, errors: b.errors, p50_ms: b.p50_ms ?? null, p95_ms: b.p95_ms ?? null, cost_usd: b.cost_usd, tokens: b.tokens_in + b.tokens_out,
  }));
  const charts = TREND_METRICS.map((t, i) => ({ ...t, data: trends[i].map((p) => ({ t: p.created_at, value: p.value ?? null, model: p.model })) })).filter(
    (c) => c.data.filter((d) => d.value !== null).length >= 2,
  );
  const grafana = process.env.NEXT_PUBLIC_GRAFANA_URL;
  const topTechniques = redteam ? Object.entries(redteam.by_technique).filter(([, c]) => c.attempts > 0).sort((x, y) => y[1].successes / y[1].attempts - x[1].successes / x[1].attempts).slice(0, 5) : [];

  return (
    <Page>
      <PageHeader
        eyebrow={
          <Link href="/" className="hover:underline">
            Fleet
          </Link>
        }
        title={
          <span className="flex flex-wrap items-center gap-3">
            {a.agent.name} <HealthBadge status={a.status} />
          </span>
        }
        description={
          <>
            {a.agent.description} <span className="text-foreground/70">{a.agent.slug} · {modelName(a.agent.model)} · {a.agent.runtime} runtime · team {a.agent.team}</span>
          </>
        }
        actions={
          <>
            {grafana && (
              <a href={grafana} target="_blank" rel="noreferrer" className="inline-flex h-9 items-center gap-1.5 rounded-lg border px-3 text-sm transition-colors hover:bg-muted">
                Grafana <ExternalLink className="size-3.5" />
              </a>
            )}
            <LaunchRun agent={a.agent.slug} agents={[{ slug: a.agent.slug, name: a.agent.name, model: a.agent.model ?? null, suites: a.agent.suites ?? [] }]} gates={config.gates} />
          </>
        }
      />

      {(a.reasons ?? []).length > 0 && (
        <ul className={`rounded-xl border px-4 py-3 text-sm ${a.status === "failing" ? "border-danger/40 bg-danger/5" : "border-warning/40 bg-warning/5"}`}>
          {(a.reasons ?? []).map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      )}

      {a.latest_run ? (
        <Section title="Latest evaluation" description={`${a.latest_run.n_results} cases · ${relTime(a.latest_run.created_at)}`}>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-[repeat(auto-fit,minmax(11rem,1fr))]">
            {HEADLINE.filter((m) => metrics[m] !== undefined).map((m) => {
              const info = metricInfo(m);
              return <MetricTile key={m} label={info.label} help={info.hint} value={info.format(metrics[m])} tone={toneFor(m, metrics[m])} />;
            })}
          </div>
        </Section>
      ) : (
        <Empty title="Never evaluated">Use “Run evaluation” to produce the first result.</Empty>
      )}

      {charts.length > 0 && (
        <Section title="Trends · 30 days" description="One point per evaluation. Dashed lines mark a model change.">
          <div className="grid gap-3 lg:grid-cols-2">
            {charts.map((c) => (
              <Card key={c.metric} className="gap-2 p-4">
                <p className="text-sm font-medium">{metricInfo(c.metric).label}</p>
                <TrendChart data={c.data} format={c.format} name={metricInfo(c.metric).label} height={180} />
              </Card>
            ))}
          </div>
        </Section>
      )}

      {Object.keys(a.online ?? {}).length > 0 && (
        <Section title="Online evaluation · 24 h" description="Reference-free checks on a sample of live requests: answers against the documents the agent retrieved, secret and personal-data leaks, refusals.">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-[repeat(auto-fit,minmax(11rem,1fr))]">
            {ONLINE_METRICS.filter((m) => a.online?.[m] !== undefined).map((m) => {
              const info = metricInfo(m);
              return <MetricTile key={m} label={info.label} help={info.hint} value={info.format(a.online![m])} tone={toneFor(m, a.online![m])} />;
            })}
          </div>
        </Section>
      )}

      <Section title="Live traffic · 24 h" description="From production traces ingested over OTLP">
        {points.length === 0 ? (
          <Empty title="No live traffic for this agent" />
        ) : (
          <>
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <MetricTile label="Requests" value={num(a.traffic?.requests)} />
              <MetricTile label="Error rate" value={pct(a.traffic?.error_rate, 2)} tone={(a.traffic?.error_rate ?? 0) > 0.05 ? "bad" : (a.traffic?.error_rate ?? 0) > 0.02 ? "warn" : "good"} />
              <MetricTile label="p95 latency" value={ms(a.traffic?.p95_ms)} />
              <MetricTile label="Spend" value={usd(a.traffic?.cost_usd, 3)} />
            </div>
            <div className="grid gap-3 lg:grid-cols-3">
              <Card className="gap-2 p-4"><p className="text-sm font-medium">Requests</p><RequestsChart data={points} height={160} /></Card>
              <Card className="gap-2 p-4"><p className="text-sm font-medium">Latency</p><LatencyChart data={points} height={160} /></Card>
              <Card className="gap-2 p-4"><p className="text-sm font-medium">Cost</p><CostChart data={points} height={160} /></Card>
            </div>
          </>
        )}
      </Section>

      <div className="grid gap-4 xl:grid-cols-2">
        <Section
          title="Red team"
          actions={redteam && <Link href={`/redteam?run=${redteam.run_id}`} className="text-xs text-primary hover:underline">Full report →</Link>}
        >
          <Card className="gap-3 p-4">
            {!redteam ? (
              <p className="text-sm text-muted-foreground">No red-team campaign yet. Launch one from “Run evaluation”.</p>
            ) : (
              <>
                <p className="text-sm">
                  <span className="tabular text-2xl font-semibold">{pct(redteam.asr, 1)}</span>{" "}
                  <span className="text-muted-foreground">of {redteam.attempts} attacks succeeded · {modelName(redteam.model)}</span>
                </p>
                <div className="grid gap-2">
                  {topTechniques.map(([name, c]) => (
                    <BarRow key={name} label={name.replaceAll("_", " ")} value={c.successes / c.attempts} max={Math.max(0.2, ...topTechniques.map(([, x]) => x.successes / x.attempts))} display={pct(c.successes / c.attempts, 0)} tone="danger" sub={`${c.successes}/${c.attempts}`} />
                  ))}
                </div>
              </>
            )}
          </Card>
        </Section>
        <Section title="Release gate history" actions={<Link href={`/gates?agent=${a.agent.slug}`} className="text-xs text-primary hover:underline">All gates →</Link>}>
          <Card className="divide-y p-0">
            {gates.length === 0 && <p className="p-4 text-sm text-muted-foreground">No gate evaluations for this agent.</p>}
            {gates.map((g) => (
              <Link key={g.id} href={`/gates/${g.id}`} className="flex items-center justify-between gap-3 px-4 py-2.5 text-sm transition-colors hover:bg-muted/50">
                <span>
                  <span className="font-medium">{g.policy}</span>
                  <span className="ml-2 text-xs text-muted-foreground">{relTime(g.created_at)}</span>
                </span>
                <VerdictBadge verdict={g.verdict} />
              </Link>
            ))}
          </Card>
        </Section>
      </div>

      <Section title="Recent runs" actions={<Link href={`/runs?agent=${a.agent.slug}`} className="text-xs text-primary hover:underline">All runs →</Link>}>
        <Card className="p-0">{runs.items.length ? <RunsTable runs={runs.items} showAgent={false} /> : <p className="p-4 text-sm text-muted-foreground">No runs yet.</p>}</Card>
      </Section>
    </Page>
  );
}
