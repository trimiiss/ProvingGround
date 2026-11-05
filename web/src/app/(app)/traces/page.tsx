import Link from "next/link";
import { Suspense } from "react";
import { FilterChips, first, Pager, withParams } from "@/components/filters";
import { Field, NativeSelect } from "@/components/native-select";
import { Empty, Page, PageHeader, PageSkeleton } from "@/components/page";
import { Chip } from "@/components/status";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { getOverview, getTraces } from "@/lib/api";
import { clip, modelName, ms, num, relTime, usd } from "@/lib/format";

export const metadata = { title: "Traces" };

const LIMIT = 30;
const WINDOWS = [
  { value: "1", label: "Last hour" },
  { value: "6", label: "Last 6 hours" },
  { value: "24", label: "Last 24 hours" },
  { value: "72", label: "Last 3 days" },
  { value: "168", label: "Last 7 days" },
  { value: "720", label: "Last 30 days" },
];

export default function TracesPage(props: PageProps<"/traces">) {
  return (
    <Suspense fallback={<PageSkeleton rows={1} />}>
      <Traces searchParams={props.searchParams} />
    </Suspense>
  );
}

async function Traces({ searchParams }: { searchParams: PageProps<"/traces">["searchParams"] }) {
  const sp = await searchParams;
  const agent = first(sp.agent) ?? "all";
  const source = first(sp.source) ?? "all";
  const hours = first(sp.hours) ?? "24";
  const q = first(sp.q) ?? "";
  const errors = first(sp.errors) === "1";
  const offset = Number(first(sp.offset) ?? 0) || 0;
  const [traces, overview] = await Promise.all([
    getTraces({
      agent: agent === "all" ? undefined : agent,
      source: source === "all" ? undefined : source,
      errors_only: errors || undefined,
      q: q || undefined,
      hours,
      limit: LIMIT + 1,
      offset,
    }),
    getOverview(),
  ]);
  const hasMore = traces.length > LIMIT;
  const rows = traces.slice(0, LIMIT);
  const cur = { agent, source, hours: hours === "24" ? undefined : hours, q, errors: errors ? "1" : undefined };
  const slugs = overview.agents.map((a) => a.agent.slug).sort();

  return (
    <Page>
      <PageHeader title="Traces" description="Production requests and evaluation cases, captured as OpenTelemetry spans. Open one to see its steps, or turn a bad one into a regression test." />
      <div className="flex flex-wrap items-end justify-between gap-4">
        <form action="/traces" className="flex flex-wrap items-end gap-2">
          {source !== "all" && <input type="hidden" name="source" value={source} />}
          {errors && <input type="hidden" name="errors" value="1" />}
          <Field label="Agent" className="w-48">
            <NativeSelect name="agent" defaultValue={agent}>
              <option value="all">All agents</option>
              {slugs.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </NativeSelect>
          </Field>
          <Field label="Window" className="w-40">
            <NativeSelect name="hours" defaultValue={hours}>
              {WINDOWS.map((w) => (
                <option key={w.value} value={w.value}>
                  {w.label}
                </option>
              ))}
            </NativeSelect>
          </Field>
          <Field label="Search" className="w-56">
            <Input name="q" defaultValue={q} placeholder="text in input or output" />
          </Field>
          <Button type="submit" variant="outline">
            Apply
          </Button>
        </form>
        <div className="flex flex-wrap items-center gap-3">
          <FilterChips current={source} options={[{ value: "all" }, { value: "live" }, { value: "eval" }]} href={(v) => withParams("/traces", cur, { source: v, offset: 0 })} />
          <FilterChips current={errors ? "1" : "0"} options={[{ value: "0", label: "all outcomes" }, { value: "1", label: "errors only" }]} href={(v) => withParams("/traces", cur, { errors: v === "1" ? "1" : undefined, offset: 0 })} />
        </div>
      </div>

      {rows.length === 0 ? (
        <Empty title="No traces in this window">Widen the window, or start the traffic generator: <code>docker compose --profile traffic up -d traffic</code></Empty>
      ) : (
        <Card className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Started</TableHead>
                <TableHead>Agent</TableHead>
                <TableHead>Input</TableHead>
                <TableHead>Model</TableHead>
                <TableHead className="text-right">Duration</TableHead>
                <TableHead className="text-right">Tokens</TableHead>
                <TableHead className="text-right">Cost</TableHead>
                <TableHead className="text-right">Spans</TableHead>
                <TableHead className="text-right">Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((t) => (
                <TableRow key={t.trace_id}>
                  <TableCell className="whitespace-nowrap">
                    <Link href={`/traces/${t.trace_id}`} className="text-primary hover:underline" title={t.started_at}>
                      {relTime(t.started_at)}
                    </Link>
                    <p className="text-[11px] text-muted-foreground">{t.source}</p>
                  </TableCell>
                  <TableCell>{t.agent ?? "—"}</TableCell>
                  <TableCell className="max-w-80 text-muted-foreground">{clip(t.input, 90)}</TableCell>
                  <TableCell className="text-muted-foreground">{modelName(t.model)}</TableCell>
                  <TableCell className="tabular text-right">{ms(t.duration_ms)}</TableCell>
                  <TableCell className="tabular text-right">{num((t.input_tokens ?? 0) + (t.output_tokens ?? 0))}</TableCell>
                  <TableCell className="tabular text-right">{usd(t.cost_usd)}</TableCell>
                  <TableCell className="tabular text-right">{t.n_spans}</TableCell>
                  <TableCell className="text-right">{t.has_error ? <Chip tone="danger">error</Chip> : <Chip tone="success">ok</Chip>}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      )}
      <Pager total={hasMore ? offset + LIMIT + 1 : offset + rows.length} limit={LIMIT} offset={offset} href={(o) => withParams("/traces", cur, { offset: o })} />
    </Page>
  );
}
