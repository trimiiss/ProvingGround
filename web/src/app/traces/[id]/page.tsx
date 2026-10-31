import Link from "next/link";
import { Suspense } from "react";
import { AddToDataset } from "@/components/add-to-dataset";
import { MetricTile } from "@/components/metric-tile";
import { Empty, Page, PageHeader, PageSkeleton, Section } from "@/components/page";
import { Chip } from "@/components/status";
import { Card } from "@/components/ui/card";
import { getTrace, orNotFound, type SpanOut } from "@/lib/api";
import { dateTime, modelName, ms, num, usd } from "@/lib/format";
import { cn } from "@/lib/utils";

export async function generateMetadata(props: PageProps<"/traces/[id]">) {
  return { title: `Trace ${(await props.params).id.slice(0, 8)}` };
}

export default function TracePage(props: PageProps<"/traces/[id]">) {
  return (
    <Suspense fallback={<PageSkeleton rows={2} />}>
      <TraceDetail params={props.params} />
    </Suspense>
  );
}

interface Row {
  span: SpanOut;
  depth: number;
  offset: number;
}

/** Order spans depth-first from the roots and compute each one's indent. Orphans (parent not captured) become roots. */
function layout(spans: SpanOut[]): { rows: Row[]; total: number } {
  if (!spans.length) return { rows: [], total: 0 };
  const t0 = Math.min(...spans.map((s) => Date.parse(s.start_time)));
  const ids = new Set(spans.map((s) => s.span_id));
  const children = new Map<string | null, SpanOut[]>();
  for (const s of spans) {
    const parent = s.parent_span_id && ids.has(s.parent_span_id) ? s.parent_span_id : null;
    children.set(parent, [...(children.get(parent) ?? []), s]);
  }
  const rows: Row[] = [];
  const walk = (parent: string | null, depth: number) => {
    for (const s of (children.get(parent) ?? []).sort((a, b) => Date.parse(a.start_time) - Date.parse(b.start_time))) {
      rows.push({ span: s, depth, offset: Date.parse(s.start_time) - t0 });
      walk(s.span_id, depth + 1);
    }
  };
  walk(null, 0);
  const total = Math.max(...rows.map((r) => r.offset + r.span.duration_ms), 1);
  return { rows, total };
}

const BAR: Record<string, string> = { invoke_agent: "bg-chart-2", chat: "bg-chart-1", execute_tool: "bg-chart-3", retrieval: "bg-chart-5", embeddings: "bg-chart-5" };

async function TraceDetail({ params }: { params: PageProps<"/traces/[id]">["params"] }) {
  const { id } = await params;
  const detail = await orNotFound(getTrace(id));
  const t = detail.trace;
  const { rows, total } = layout(detail.spans);
  const root = detail.spans.find((s) => s.operation === "invoke_agent");
  const hasInput = Boolean(root?.attributes?.["pground.input"]);

  return (
    <Page>
      <PageHeader
        eyebrow={
          <Link href="/traces" className="hover:underline">
            Traces
          </Link>
        }
        title={<span className="font-mono text-xl">{t.trace_id}</span>}
        description={`${t.agent ?? "unknown agent"} · ${dateTime(t.started_at)} · ${t.source === "eval" ? "evaluation case" : "production request"}`}
        actions={hasInput && t.source === "live" ? <AddToDataset traceId={t.trace_id} observed={t.output} /> : undefined}
      />

      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <MetricTile label="Duration" value={ms(t.duration_ms)} />
        <MetricTile label="Status" value={t.has_error ? "Error" : "OK"} tone={t.has_error ? "bad" : "good"} />
        <MetricTile label="Model" value={modelName(t.model)} />
        <MetricTile label="Tokens" value={num((t.input_tokens ?? 0) + (t.output_tokens ?? 0))} sub={`${num(t.input_tokens)} in · ${num(t.output_tokens)} out`} />
        <MetricTile label="Cost" value={usd(t.cost_usd, 5)} />
      </div>

      {(t.input || t.output) && (
        <div className="grid gap-3 md:grid-cols-2">
          <Block title="Input" text={t.input} />
          <Block title="Output" text={t.output} />
        </div>
      )}

      <Section title={`Timeline · ${rows.length} spans`} description="Each bar is one step, positioned by when it started and sized by how long it took. Open a span for its attributes.">
        {rows.length === 0 ? (
          <Empty title="No spans were stored for this trace" />
        ) : (
          <Card className="gap-0 divide-y p-0">
            {rows.map(({ span: s, depth, offset }) => (
              <details key={s.span_id} className="group">
                <summary className="grid cursor-pointer list-none grid-cols-[minmax(10rem,18rem)_1fr_4.5rem] items-center gap-3 px-4 py-2 text-sm hover:bg-muted/40 focus-visible:bg-muted/40 focus-visible:outline-none [&::-webkit-details-marker]:hidden">
                  <span className="flex min-w-0 items-center gap-1.5" style={{ paddingLeft: depth * 16 }}>
                    <span className="truncate font-medium" title={s.name}>
                      {s.name}
                    </span>
                    {s.status === "error" && <Chip tone="danger" dot={false}>error</Chip>}
                  </span>
                  <span className="relative h-5 rounded bg-muted/50">
                    <span
                      className={cn("absolute inset-y-1 min-w-0.5 rounded-sm", s.status === "error" ? "bg-danger" : (BAR[s.operation ?? ""] ?? "bg-chart-6"))}
                      style={{ left: `${(offset / total) * 100}%`, width: `${Math.max(0.4, (s.duration_ms / total) * 100)}%` }}
                    />
                  </span>
                  <span className="tabular text-right text-xs text-muted-foreground">{ms(s.duration_ms)}</span>
                </summary>
                <div className="grid gap-2 bg-muted/30 px-4 py-3 text-xs">
                  <p className="flex flex-wrap gap-x-4 gap-y-1 text-muted-foreground">
                    <span>operation <b className="font-medium text-foreground">{s.operation ?? "—"}</b></span>
                    <span>kind <b className="font-medium text-foreground">{s.kind}</b></span>
                    {s.model && <span>model <b className="font-medium text-foreground">{s.model}</b></span>}
                    {s.cost_usd ? <span>cost <b className="font-medium text-foreground">{usd(s.cost_usd, 5)}</b></span> : null}
                    {s.input_tokens ? <span>tokens <b className="font-medium text-foreground">{s.input_tokens} in / {s.output_tokens ?? 0} out</b></span> : null}
                    <span>starts +{ms(offset)}</span>
                  </p>
                  <dl className="grid gap-1.5">
                    {Object.entries(s.attributes ?? {}).map(([k, v]) => (
                      <div key={k} className="grid grid-cols-[minmax(8rem,14rem)_1fr] gap-3">
                        <dt className="truncate font-mono text-muted-foreground" title={k}>
                          {k}
                        </dt>
                        <dd className="font-mono break-words whitespace-pre-wrap">{typeof v === "string" ? v : JSON.stringify(v)}</dd>
                      </div>
                    ))}
                  </dl>
                </div>
              </details>
            ))}
          </Card>
        )}
      </Section>

      {(detail.online_scores ?? []).length > 0 && (
        <Section title="Online evaluation" description="Reference-free scores computed on this live request.">
          <Card className="divide-y p-0">
            {detail.online_scores!.map((s, i) => (
              <p key={i} className="flex items-center justify-between gap-3 px-4 py-2.5 text-sm">
                <span>{String((s as Record<string, unknown>).metric ?? "score")}</span>
                <span className="tabular font-medium">{String((s as Record<string, unknown>).value ?? "—")}</span>
              </p>
            ))}
          </Card>
        </Section>
      )}
    </Page>
  );
}

function Block({ title, text }: { title: string; text?: string | null }) {
  return (
    <div className="rounded-xl border bg-card p-4">
      <p className="mb-1 text-[11px] font-medium text-muted-foreground uppercase">{title}</p>
      <p className="max-h-56 overflow-auto text-sm break-words whitespace-pre-wrap">{text || "—"}</p>
    </div>
  );
}
