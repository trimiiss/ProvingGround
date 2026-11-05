import Link from "next/link";
import { Suspense } from "react";
import { BarRow, heat } from "@/components/charts/visuals";
import { first } from "@/components/filters";
import { MetricTile } from "@/components/metric-tile";
import { Field, NativeSelect } from "@/components/native-select";
import { Empty, Page, PageHeader, PageSkeleton, Section } from "@/components/page";
import { Chip } from "@/components/status";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { type AttackCell, getOverview, getRedteam, getRun, getRuns, orNull, type RedteamSummary } from "@/lib/api";
import { dateTime, modelName, num, pct } from "@/lib/format";
import { metricInfo, toneClass, toneFor } from "@/lib/metrics";

export const metadata = { title: "Red team" };

export default function RedTeamPage(props: PageProps<"/redteam">) {
  return (
    <Suspense fallback={<PageSkeleton rows={2} />}>
      <RedTeam searchParams={props.searchParams} />
    </Suspense>
  );
}

const label = (s: string) => s.replaceAll("_", " ");
const SHOWN = 6;
const rate = (c: AttackCell) => (c.attempts ? c.successes / c.attempts : 0);

async function RedTeam({ searchParams }: { searchParams: PageProps<"/redteam">["searchParams"] }) {
  const sp = await searchParams;
  const overview = await getOverview();
  // With no choice made, open the agent that is losing the most attacks: that is what a reviewer wants to see first.
  const worst = [...overview.agents].filter((a) => a.asr !== null && a.asr !== undefined).sort((a, b) => (b.asr ?? 0) - (a.asr ?? 0))[0];
  const runParam = first(sp.run);
  const runForParam = runParam ? await orNull(getRun(runParam)) : null;
  const agent = first(sp.agent) ?? runForParam?.agent ?? worst?.agent.slug;
  // An uploaded bot's normal run carries the built-in attacks, so it counts as a campaign too.
  const [dedicated, uploaded] = agent
    ? await Promise.all([getRuns({ agent, kind: "redteam", limit: 30 }), getRuns({ agent, label: "attacks=builtin", limit: 30 })])
    : [null, null];
  const campaigns = [...(dedicated?.items ?? []), ...(uploaded?.items ?? [])].sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 30);
  const selected = runParam && campaigns.some((c) => c.id === runParam) ? runParam : campaigns[0]?.id;
  const [summary, run] = selected ? await Promise.all([orNull(getRedteam(selected)), getRun(selected)]) : [null, null];
  const canaries = ((run?.target ?? {}) as { canaries?: string[] }).canaries ?? [];
  const slugs = overview.agents.map((a) => a.agent.slug).sort();

  return (
    <Page>
      <PageHeader title="Red team" description="Each attack combines a goal (what the attacker wants), a technique (how they ask) and a converter (how the request is disguised). An attack counts as won only when a deterministic detector sees the violation: a leaked canary, a forbidden tool call, or planted marker." />

      <Card className="p-4">
        <form action="/redteam" className="flex flex-wrap items-end gap-3">
          <Field label="Agent" className="w-52">
            <NativeSelect name="agent" defaultValue={agent}>
              {slugs.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </NativeSelect>
          </Field>
          {campaigns.length > 0 && (
            <Field label="Campaign" className="w-96">
              <NativeSelect name="run" defaultValue={selected}>
                {campaigns.map((c) => (
                  <option key={c.id} value={c.id}>
                    {dateTime(c.created_at)} · {modelName(c.model)} · {pct(c.metrics?.["safety.attack_success_rate"], 1)} won · {c.n_results} {c.kind === "redteam" ? "attacks" : "cases"}
                  </option>
                ))}
              </NativeSelect>
            </Field>
          )}
          <Button type="submit">Show</Button>
        </form>
      </Card>

      {!summary ? (
        <Empty title={agent ? `No red-team campaign for ${agent}` : "No red-team campaigns yet"}>
          Open an agent and choose “Red-team campaign” under Run evaluation.
        </Empty>
      ) : (
        <Report s={summary} canaries={canaries} />
      )}
    </Page>
  );
}

function Report({ s, canaries }: { s: RedteamSummary; canaries: string[] }) {
  const goals = Object.keys(s.by_goal);
  const techniques = Object.keys(s.grid);
  const tone = toneFor("safety.attack_success_rate", s.asr);
  const sortRate = (m: Record<string, AttackCell>) => Object.entries(m).sort((a, b) => rate(b[1]) - rate(a[1]));
  return (
    <>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <MetricTile label="Attack success rate" help={metricInfo("safety.attack_success_rate").hint} value={pct(s.asr, 1)} tone={tone} sub={s.asr_ci?.[0] != null && s.asr_ci?.[1] != null ? `95% CI ${pct(s.asr_ci[0], 1)} – ${pct(s.asr_ci[1], 1)}` : undefined} />
        <MetricTile label="Attacks that succeeded" value={`${num(s.successes)} / ${num(s.attempts)}`} sub={s.not_scored ? `${s.not_scored} not scored (errors)` : undefined} />
        <MetricTile label="Model" value={modelName(s.model)} sub={s.target} />
        <MetricTile label="False refusals" value={s.false_refusal_rate === null ? "—" : pct(s.false_refusal_rate, 1)} sub="benign requests wrongly refused" tone={s.false_refusal_rate !== null && s.false_refusal_rate > 0.1 ? "warn" : "neutral"} />
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Breakdown title="By goal" data={sortRate(s.by_goal)} />
        <Breakdown title="By technique" data={sortRate(s.by_technique)} />
        <Breakdown title="By converter" data={sortRate(s.by_converter)} />
      </div>

      <Section title="Technique × goal" description="Share of attacks that succeeded. Darker red means the defence fails more often. Hover a cell for counts.">
        <Card className="overflow-x-auto p-0">
          <table className="w-full min-w-[640px] border-collapse text-xs">
            <thead>
              <tr>
                <th className="p-2 text-left font-medium text-muted-foreground">Technique</th>
                {goals.map((g) => (
                  <th key={g} className="p-2 text-center font-medium text-muted-foreground">
                    {label(g)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {techniques.map((t) => (
                <tr key={t} className="border-t">
                  <th className="p-2 text-left font-medium whitespace-nowrap">{label(t)}</th>
                  {goals.map((g) => {
                    const c = s.grid[t]?.[g];
                    return (
                      <td key={g} className="p-0.5">
                        {c && c.attempts > 0 ? (
                          <div className="tabular rounded-md py-2 text-center font-medium" style={heat(rate(c))} title={`${c.successes} of ${c.attempts} attacks succeeded`}>
                            {pct(rate(c), 0)}
                          </div>
                        ) : (
                          <div className="py-2 text-center text-muted-foreground/50">·</div>
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </Section>

      <Section title="Successful attacks" description="The detector evidence is shown with each attack. Leaked secrets are highlighted in the response.">
        {s.top_successes.length === 0 ? (
          <Empty title="No attack succeeded">Every attack in this campaign was defended.</Empty>
        ) : (
          <div className="grid gap-3">
            {s.top_successes.slice(0, SHOWN).map((a) => (
              <Card key={a.case_id} className="gap-3 p-4">
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <Chip tone="danger" dot={false}>{label(a.goal)}</Chip>
                  <Chip tone="muted" dot={false}>{label(a.technique)}</Chip>
                  {a.converter !== "none" && <Chip tone="info" dot={false}>{a.converter}</Chip>}
                  {a.evidence.map((e) => (
                    <Chip key={e} tone="warning" dot={false}>{label(e)}</Chip>
                  ))}
                  <Link href={`/runs/${s.run_id}?q=${encodeURIComponent(a.case_id)}&outcome=any`} className="ml-auto font-mono text-muted-foreground hover:text-primary hover:underline">
                    {a.case_id}
                  </Link>
                </div>
                <div className="grid gap-3 md:grid-cols-2">
                  <div className="rounded-lg bg-muted/60 p-3">
                    <p className="mb-1 text-[11px] font-medium text-muted-foreground uppercase">Attack prompt</p>
                    <p className="text-sm whitespace-pre-wrap [overflow-wrap:anywhere]">{a.prompt}</p>
                  </div>
                  <div className="rounded-lg bg-danger/6 p-3">
                    <p className="mb-1 text-[11px] font-medium text-muted-foreground uppercase">Agent response</p>
                    <p className="text-sm whitespace-pre-wrap [overflow-wrap:anywhere]">
                      <Highlight text={a.response} terms={canaries} />
                    </p>
                  </div>
                </div>
              </Card>
            ))}
            {s.successes > SHOWN && (
              <Link href={`/runs/${s.run_id}?suite=all`} className="text-sm text-primary hover:underline">
                All {s.successes} successful attacks are listed as failed cases in the run →
              </Link>
            )}
          </div>
        )}
      </Section>
    </>
  );
}

function Breakdown({ title, data }: { title: string; data: [string, AttackCell][] }) {
  const max = Math.max(0.1, ...data.map(([, c]) => rate(c)));
  return (
    <Section title={title}>
      <Card className="gap-2.5 p-4">
        {data.map(([name, c]) => (
          <BarRow key={name} label={label(name)} value={rate(c)} max={max} display={pct(rate(c), 0)} tone={rate(c) > 0.1 ? "danger" : rate(c) > 0.02 ? "warning" : "success"} sub={`${c.successes}/${c.attempts}`} />
        ))}
      </Card>
    </Section>
  );
}

/** Marks every occurrence of the given secrets inside a piece of text. */
function Highlight({ text, terms }: { text: string; terms: string[] }) {
  const live = terms.filter(Boolean);
  if (!live.length) return <>{text}</>;
  const re = new RegExp(`(${live.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "g");
  return (
    <>
      {text.split(re).map((part, i) =>
        live.includes(part) ? (
          <mark key={i} className={`rounded bg-danger/20 px-1 font-mono text-[13px] ${toneClass.bad}`}>
            {part}
          </mark>
        ) : (
          part
        ),
      )}
    </>
  );
}
