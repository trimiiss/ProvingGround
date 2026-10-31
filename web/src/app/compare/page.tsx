import { Suspense } from "react";
import { Delta } from "@/components/metric-tile";
import { first } from "@/components/filters";
import { Field, NativeSelect } from "@/components/native-select";
import { GateViewSection } from "./gate-section";
import { Empty, Page, PageHeader, PageSkeleton, Section } from "@/components/page";
import { Chip } from "@/components/status";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { type Comparison, getCompare, getConfig, getOverview, getRun, getRuns, orNull, type RunBrief } from "@/lib/api";
import { dateTime, modelName, num, shortId } from "@/lib/format";
import { FAMILY_LABEL, metricInfo } from "@/lib/metrics";

export const metadata = { title: "Compare" };

export default function ComparePage(props: PageProps<"/compare">) {
  return (
    <Suspense fallback={<PageSkeleton rows={2} />}>
      <Compare searchParams={props.searchParams} />
    </Suspense>
  );
}

const runLabel = (r: RunBrief) => `${dateTime(r.created_at)} · ${modelName(r.model)} · ${num(r.n_results)} cases · ${shortId(r.id)}`;

async function Compare({ searchParams }: { searchParams: PageProps<"/compare">["searchParams"] }) {
  const sp = await searchParams;
  let agent = first(sp.agent);
  const baselineId = first(sp.baseline);
  const candidateId = first(sp.candidate);
  const config = await getConfig();
  const policy = first(sp.policy) ?? (config.gates.includes("release") ? "release" : (config.gates[0] ?? "release"));
  if (!agent && candidateId) agent = (await orNull(getRun(candidateId)))?.agent;

  const [overview, runs] = await Promise.all([getOverview(), agent ? getRuns({ agent, kind: "eval", limit: 80 }) : Promise.resolve(null)]);
  const slugs = overview.agents.map((a) => a.agent.slug).sort();
  const items = runs?.items ?? [];
  const baselineValue = baselineId ?? items[1]?.id;
  const candidateValue = candidateId ?? items[0]?.id;
  const ready = !!(baselineId && candidateId);
  const comparison = ready ? await orNull(getCompare(baselineId!, candidateId!)) : null;

  return (
    <Page>
      <PageHeader title="Compare" description="Two runs of the same agent, paired case by case. Differences carry confidence intervals, so noise is not mistaken for a regression." />

      <Card className="p-4">
        <form action="/compare" className="flex flex-wrap items-end gap-3">
          <Field label="Agent" className="w-52">
            <NativeSelect name="agent" defaultValue={agent ?? ""}>
              <option value="" disabled>
                Choose an agent…
              </option>
              {slugs.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </NativeSelect>
          </Field>
          {agent && (
            <>
              <Field label="Baseline (the known-good run)" className="w-80">
                <NativeSelect name="baseline" defaultValue={baselineValue}>
                  {items.map((r) => (
                    <option key={r.id} value={r.id}>
                      {runLabel(r)}
                    </option>
                  ))}
                </NativeSelect>
              </Field>
              <Field label="Candidate (the change under test)" className="w-80">
                <NativeSelect name="candidate" defaultValue={candidateValue}>
                  {items.map((r) => (
                    <option key={r.id} value={r.id}>
                      {runLabel(r)}
                    </option>
                  ))}
                </NativeSelect>
              </Field>
              <Field label="Gate policy" className="w-36">
                <NativeSelect name="policy" defaultValue={policy}>
                  {config.gates.map((g) => (
                    <option key={g} value={g}>
                      {g}
                    </option>
                  ))}
                </NativeSelect>
              </Field>
            </>
          )}
          <Button type="submit">{agent ? "Compare" : "Choose"}</Button>
        </form>
      </Card>

      {!agent && <Empty title="Pick an agent to compare two of its runs">Or open a run and use “Compare with previous”.</Empty>}
      {agent && !ready && <Empty title="Choose a baseline and a candidate" />}
      {ready && !comparison && <Empty title="Could not compare these runs">One of them may have been deleted.</Empty>}
      {comparison && (
        <>
          <Suspense fallback={<div className="h-32 animate-pulse rounded-xl bg-muted" />}>
            <GateViewSection policy={policy} candidate={comparison.candidate_id} baseline={comparison.baseline_id} />
          </Suspense>
          <DeltaTable c={comparison} />
          <Flips c={comparison} />
        </>
      )}
    </Page>
  );
}

function DeltaTable({ c }: { c: Comparison }) {
  const rows = Object.values(c.deltas).sort((a, b) => a.metric.localeCompare(b.metric));
  return (
    <Section
      title="Metric differences"
      description={`${num(c.n_common_cases)} paired cases · ${Math.round(c.confidence * 100)}% confidence · ${c.n_baseline_only} baseline-only, ${c.n_candidate_only} candidate-only`}
    >
      {c.comparability.length > 0 && (
        <ul className="grid gap-1.5">
          {c.comparability.map((i) => (
            <li key={i.message} className={`rounded-lg border px-3 py-2 text-sm ${i.severity === "block" ? "border-danger/40 bg-danger/5" : "border-warning/40 bg-warning/5"}`}>
              <Chip tone={i.severity === "block" ? "danger" : "warning"} dot={false} className="mr-2">
                {i.severity === "block" ? "blocks comparison" : "caution"}
              </Chip>
              {i.message}
            </li>
          ))}
        </ul>
      )}
      <Card className="p-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Metric</TableHead>
              <TableHead className="text-right">Baseline</TableHead>
              <TableHead className="text-right">Candidate</TableHead>
              <TableHead className="text-right">Change</TableHead>
              <TableHead className="text-right">CI of change</TableHead>
              <TableHead className="text-right">p</TableHead>
              <TableHead className="text-right">n</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((d, i) => {
              const info = metricInfo(d.metric);
              const fam = d.metric.split(".")[0];
              const showFam = i === 0 || rows[i - 1].metric.split(".")[0] !== fam;
              const significant = d.ci_low !== null && d.ci_low !== undefined && d.ci_high !== null && d.ci_high !== undefined && (d.ci_low > 0 || d.ci_high < 0);
              return (
                <TableRow key={d.metric}>
                  <TableCell>
                    <span className="inline-block w-28 text-xs text-muted-foreground">{showFam ? (FAMILY_LABEL[fam] ?? fam) : ""}</span>
                    <span title={d.metric}>{info.label}</span>
                  </TableCell>
                  <TableCell className="tabular text-right text-muted-foreground">{info.format(d.baseline)}</TableCell>
                  <TableCell className="tabular text-right font-medium">{info.format(d.candidate)}</TableCell>
                  <TableCell className="text-right">
                    <Delta delta={d.delta} format={(v) => info.format(v)} lowerIsBetter={info.lowerIsBetter} className={significant ? "" : "opacity-60"} />
                  </TableCell>
                  <TableCell className="tabular text-right text-xs text-muted-foreground">
                    {d.ci_low !== null && d.ci_low !== undefined && d.ci_high !== null && d.ci_high !== undefined ? `${signed(d.ci_low, info.format)} … ${signed(d.ci_high, info.format)}` : "—"}
                  </TableCell>
                  <TableCell className="tabular text-right text-xs text-muted-foreground">{d.p_value === null || d.p_value === undefined ? "—" : d.p_value < 0.001 ? "<0.001" : d.p_value.toFixed(3)}</TableCell>
                  <TableCell className="tabular text-right text-xs text-muted-foreground">{num(d.n)}</TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </Card>
      <p className="text-xs text-muted-foreground">Faded changes have a confidence interval that includes zero, so they cannot be told apart from noise at this sample size.</p>
    </Section>
  );
}

const signed = (v: number, f: (v: number) => string) => `${v < 0 ? "−" : v > 0 ? "+" : ""}${f(Math.abs(v))}`;

function Flips({ c }: { c: Comparison }) {
  const regressions = c.flips.filter((f) => f.kind === "regression");
  const fixes = c.flips.filter((f) => f.kind === "fix");
  return (
    <Section title={`Flipped cases · ${regressions.length} regressions, ${fixes.length} fixes`} description="Cases whose pass/fail outcome changed between the runs. Critical cases first.">
      {c.flips.length === 0 ? (
        <Empty title="No case changed its outcome" />
      ) : (
        <div className="grid gap-3">
          {c.flips.slice(0, 12).map((f) => (
            <Card key={`${f.suite}/${f.case_id}`} className="gap-3 p-4">
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <Chip tone={f.kind === "regression" ? "danger" : "success"} dot={false}>
                  {f.kind === "regression" ? "regression" : "fixed"}
                </Chip>
                {f.critical && <Chip tone="warning" dot={false}>critical</Chip>}
                <span className="font-mono text-muted-foreground">{f.suite} / {f.case_id}</span>
              </div>
              <p className="text-sm font-medium">{f.preview}</p>
              <div className="grid gap-3 md:grid-cols-2">
                <Side title="Baseline" text={f.baseline_text} />
                <Side title="Candidate" text={f.candidate_text} />
              </div>
            </Card>
          ))}
          {c.flips.length > 12 && <p className="text-xs text-muted-foreground">Showing 12 of {c.flips.length}. The CLI report lists every flip.</p>}
        </div>
      )}
    </Section>
  );
}

function Side({ title, text }: { title: string; text: string }) {
  return (
    <div className="rounded-lg bg-muted/60 p-3">
      <p className="mb-1 text-[11px] font-medium text-muted-foreground uppercase">{title}</p>
      <p className="text-sm whitespace-pre-wrap">{text || "(empty)"}</p>
    </div>
  );
}
