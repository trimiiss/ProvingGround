import { FileText, GitCompareArrows } from "lucide-react";
import Link from "next/link";
import { Suspense } from "react";
import { CaseTable } from "@/components/case-table";
import { FilterChips, first, Pager, withParams } from "@/components/filters";
import { MetricTile } from "@/components/metric-tile";
import { Field, NativeSelect } from "@/components/native-select";
import { Page, PageHeader, PageSkeleton, Section } from "@/components/page";
import { KindBadge } from "@/components/status";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { getResults, getRun, getRuns, orNotFound } from "@/lib/api";
import { dateTime, modelName, num, pct, usd } from "@/lib/format";
import { FAMILY_LABEL, HEADLINE, metricInfo, toneFor } from "@/lib/metrics";

export async function generateMetadata(props: PageProps<"/runs/[id]">) {
  return { title: `Run ${(await props.params).id.slice(-12)}` };
}

const LIMIT = 25;

export default function RunPage(props: PageProps<"/runs/[id]">) {
  return (
    <Suspense fallback={<PageSkeleton rows={3} />}>
      <RunDetail params={props.params} searchParams={props.searchParams} />
    </Suspense>
  );
}

interface MetricSummary {
  mean?: number;
  ci_low?: number | null;
  ci_high?: number | null;
  n?: number;
  pass_rate?: number | null;
}
interface SuiteInfo {
  name: string;
  n_cases: number;
  scorers: string[];
  dataset_version?: string;
}

async function RunDetail({ params, searchParams }: { params: PageProps<"/runs/[id]">["params"]; searchParams: PageProps<"/runs/[id]">["searchParams"] }) {
  const { id } = await params;
  const sp = await searchParams;
  const outcome = first(sp.outcome) ?? "failed";
  const suite = first(sp.suite) ?? "all";
  const q = first(sp.q) ?? "";
  const critical = first(sp.critical) === "1";
  const offset = Number(first(sp.offset) ?? 0) || 0;

  const run = await orNotFound(getRun(id));
  const suites = (run.suites ?? []) as unknown as SuiteInfo[];
  const [results, siblings] = await Promise.all([
    getResults(id, {
      suite: suite === "all" ? undefined : suite,
      passed: outcome === "failed" ? false : outcome === "passed" ? true : undefined,
      status: outcome === "errors" ? "error" : undefined,
      critical: critical || undefined,
      q: q || undefined,
      limit: LIMIT,
      offset,
    }),
    getRuns({ agent: run.agent, kind: run.kind, limit: 40 }),
  ]);
  // The previous run of the same agent and kind is the natural baseline for a comparison.
  const idx = siblings.items.findIndex((r) => r.id === run.id);
  const previous = idx >= 0 ? siblings.items[idx + 1] : undefined;

  const metrics = run.metrics ?? {};
  const summary = ((run.summary ?? {}) as { metrics?: Record<string, MetricSummary> }).metrics ?? {};
  const families = new Map<string, string[]>();
  for (const m of Object.keys(metrics).sort()) {
    const fam = m.split(".")[0];
    families.set(fam, [...(families.get(fam) ?? []), m]);
  }
  const cur = { outcome, suite, q, critical: critical ? "1" : undefined };
  const base = `/runs/${id}`;
  const settings = (run.settings ?? {}) as Record<string, unknown>;
  const git = (run.git ?? null) as { ref?: string; sha?: string; dirty?: boolean } | null;
  const target = (run.target ?? {}) as { fingerprint?: string; kind?: string };

  return (
    <Page>
      <PageHeader
        eyebrow={
          <span className="flex items-center gap-1.5">
            <Link href="/runs" className="hover:underline">
              Runs
            </Link>{" "}
            / <Link href={`/agents/${run.agent}`} className="hover:underline">{run.agent}</Link>
          </span>
        }
        title={
          <span className="flex flex-wrap items-center gap-3">
            <span className="font-mono text-xl">{run.id}</span>
            <KindBadge kind={run.kind} />
          </span>
        }
        description={`${modelName(run.model)} · ${dateTime(run.created_at)} · ${num(run.n_results)} results · ${usd(run.cost_usd)}`}
        actions={
          <>
            <a href={`/api/pg/v1/runs/${run.id}/report?format=html`} target="_blank" rel="noreferrer" className="inline-flex h-9 items-center gap-1.5 rounded-lg border px-3 text-sm transition-colors hover:bg-muted">
              <FileText className="size-4" /> HTML report
            </a>
            {run.kind === "redteam" && (
              <Link href={`/redteam?run=${run.id}`} className="inline-flex h-9 items-center gap-1.5 rounded-lg border px-3 text-sm transition-colors hover:bg-muted">
                Attack breakdown
              </Link>
            )}
            {previous && (
              <Link href={`/compare?baseline=${previous.id}&candidate=${run.id}`} className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-primary px-3 text-sm text-primary-foreground transition-opacity hover:opacity-90">
                <GitCompareArrows className="size-4" /> Compare with previous
              </Link>
            )}
          </>
        }
      />

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <MetricTile label="Pass rate" value={pct(run.pass_rate, 1)} sub={`${num(run.n_results)} results`} />
        {HEADLINE.filter((m) => metrics[m] !== undefined && m !== "factuality.hallucination_rate").slice(0, 7).map((m) => {
          const info = metricInfo(m);
          const s = summary[m];
          return (
            <MetricTile
              key={m}
              label={info.label}
              value={info.format(metrics[m])}
              tone={toneFor(m, metrics[m])}
              sub={s?.ci_low !== undefined && s?.ci_low !== null && s?.ci_high !== undefined && s?.ci_high !== null ? `95% CI ${info.format(s.ci_low)} – ${info.format(s.ci_high)}` : undefined}
            />
          );
        })}
      </div>

      <div className="grid gap-4 xl:grid-cols-[2fr_1fr]">
        <Section title="All metrics" description="Means with bootstrap 95% confidence intervals; n is the number of scored cases.">
          <Card className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Metric</TableHead>
                  <TableHead className="text-right">Value</TableHead>
                  <TableHead className="text-right">95% CI</TableHead>
                  <TableHead className="text-right">n</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {[...families.entries()].map(([fam, names]) =>
                  names.map((m, i) => {
                    const info = metricInfo(m);
                    const s = summary[m];
                    return (
                      <TableRow key={m}>
                        <TableCell>
                          <span className="inline-block w-28 text-xs text-muted-foreground">{i === 0 ? (FAMILY_LABEL[fam] ?? fam) : ""}</span>
                          <span title={m}>{info.label}</span>
                        </TableCell>
                        <TableCell className="tabular text-right font-medium">{info.format(metrics[m])}</TableCell>
                        <TableCell className="tabular text-right text-muted-foreground">
                          {s?.ci_low !== undefined && s?.ci_low !== null && s?.ci_high !== undefined && s?.ci_high !== null ? `${info.format(s.ci_low)} – ${info.format(s.ci_high)}` : "—"}
                        </TableCell>
                        <TableCell className="tabular text-right text-muted-foreground">{s?.n !== undefined ? num(s.n) : "—"}</TableCell>
                      </TableRow>
                    );
                  }),
                )}
              </TableBody>
            </Table>
          </Card>
        </Section>

        <div className="flex flex-col gap-4">
          <Section title="Suites">
            <Card className="divide-y p-0">
              {suites.map((s) => (
                <div key={s.name} className="px-4 py-2.5 text-sm">
                  <p className="flex items-center justify-between font-medium">
                    {s.name} <span className="tabular text-xs font-normal text-muted-foreground">{s.n_cases} cases</span>
                  </p>
                  <p className="truncate text-xs text-muted-foreground" title={s.scorers.join(", ")}>
                    {s.scorers.join(" · ")}
                  </p>
                  {s.dataset_version && <p className="font-mono text-[11px] text-muted-foreground/70">dataset {s.dataset_version}</p>}
                </div>
              ))}
            </Card>
          </Section>
          <Section title="Reproducibility">
            <Card className="gap-1.5 p-4 text-xs">
              <Row k="Target fingerprint" v={target.fingerprint} mono />
              <Row k="Judge" v={run.judge_model ?? "none"} />
              <Row k="Git" v={git ? `${git.ref ?? "?"} @ ${(git.sha ?? "").slice(0, 8)}${git.dirty ? " (dirty)" : ""}` : "—"} mono />
              <Row k="Seed / repeats" v={`${String(settings.seed ?? "—")} / ${String(settings.repeats ?? 1)}`} />
              <Row k="Sampled" v={settings.sample ? `${String(settings.sample)} per suite` : "all cases"} />
              <Row k="Response cache" v={settings.use_cache ? "on" : "off"} />
            </Card>
          </Section>
        </div>
      </div>

      <Section title={`Cases (${num(results.total)})`} description="Critical cases and failures are listed first. Click a row for the full input, output, context and scoring.">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <form action={base} className="flex flex-wrap items-end gap-2">
            <input type="hidden" name="outcome" value={outcome} />
            {critical && <input type="hidden" name="critical" value="1" />}
            <Field label="Suite" className="w-56">
              <NativeSelect name="suite" defaultValue={suite}>
                <option value="all">All suites</option>
                {suites.map((s) => (
                  <option key={s.name} value={s.name}>
                    {s.name}
                  </option>
                ))}
              </NativeSelect>
            </Field>
            <Field label="Search" className="w-64">
              <Input name="q" defaultValue={q} placeholder="input, output or case id" />
            </Field>
            <Button type="submit" variant="outline">
              Apply
            </Button>
          </form>
          <div className="flex flex-wrap items-center gap-3">
            <FilterChips
              current={outcome}
              options={[{ value: "failed" }, { value: "passed" }, { value: "errors" }, { value: "any" }]}
              href={(v) => withParams(base, cur, { outcome: v === "failed" ? undefined : v, offset: 0 })}
            />
            <FilterChips current={critical ? "1" : "0"} options={[{ value: "0", label: "any" }, { value: "1", label: "critical only" }]} href={(v) => withParams(base, cur, { critical: v === "1" ? "1" : undefined, offset: 0 })} />
          </div>
        </div>
        <Card className="p-0">
          <CaseTable items={results.items} />
        </Card>
        <Pager total={results.total} limit={LIMIT} offset={offset} href={(o) => withParams(base, cur, { offset: o })} />
      </Section>
    </Page>
  );
}

function Row({ k, v, mono }: { k: string; v: string | undefined; mono?: boolean }) {
  return (
    <p className="flex items-baseline justify-between gap-3">
      <span className="text-muted-foreground">{k}</span>
      <span className={mono ? "truncate font-mono" : "truncate"}>{v ?? "—"}</span>
    </p>
  );
}
