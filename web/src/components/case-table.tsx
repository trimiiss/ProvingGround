"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { Chip, PassMark } from "@/components/status";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { components } from "@/lib/api-types";
import { clip, ms, usd } from "@/lib/format";
import { metricInfo } from "@/lib/metrics";

type Brief = components["schemas"]["ResultBrief"];
type Detail = components["schemas"]["ResultDetail"];

interface ScoreRow {
  metric: string;
  value: number;
  passed: boolean | null;
  reason?: string;
  scorer?: string;
  judge_model?: string | null;
  details?: Record<string, unknown>;
}
interface Message {
  role: string;
  content: string | null;
  tool_calls?: { name: string; arguments?: unknown }[];
}
interface OutputPayload {
  text?: string;
  error?: string | null;
  status?: string;
  tool_calls?: { name: string; arguments?: unknown }[];
  messages?: Message[];
  retrieved_context?: string[];
  model_served?: string;
  model_requested?: string;
  stop_reason?: string;
  attempts?: number;
  usage?: Record<string, number>;
}

const STATUS_TONE: Record<string, "success" | "warning" | "danger" | "muted"> = { ok: "success", refused: "muted", truncated: "warning", timeout: "danger", error: "danger", model_mismatch: "danger" };

export function CaseTable({ items }: { items: Brief[] }) {
  const [open, setOpen] = useState<Brief | null>(null);
  return (
    <>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-16">Result</TableHead>
            <TableHead>Case</TableHead>
            <TableHead>Input</TableHead>
            <TableHead>Output</TableHead>
            <TableHead className="text-right">Latency</TableHead>
            <TableHead className="text-right">Cost</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((r) => (
            <TableRow key={r.id} tabIndex={0} role="button" className="cursor-pointer focus-visible:bg-muted/60 focus-visible:outline-none" onClick={() => setOpen(r)} onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), setOpen(r))}>
              <TableCell>
                {r.status === "ok" || r.status === "refused" ? <PassMark passed={r.passed} /> : <Chip tone={STATUS_TONE[r.status] ?? "muted"} dot={false}>{r.status.replace("_", " ")}</Chip>}
              </TableCell>
              <TableCell className="max-w-48">
                <p className="flex items-center gap-1 truncate font-mono text-xs">
                  {r.critical && <AlertTriangle className="size-3.5 shrink-0 text-warning" aria-label="critical case" />}
                  <span className="truncate">{r.case_id}</span>
                </p>
                <p className="truncate text-xs text-muted-foreground">{r.suite}</p>
              </TableCell>
              <TableCell className="max-w-72 text-muted-foreground">{clip(r.input_preview, 110)}</TableCell>
              <TableCell className="max-w-72">{clip(r.output_text, 110)}</TableCell>
              <TableCell className="tabular text-right">{ms(r.latency_ms)}</TableCell>
              <TableCell className="tabular text-right">{usd(r.cost_usd)}</TableCell>
            </TableRow>
          ))}
          {items.length === 0 && (
            <TableRow>
              <TableCell colSpan={6} className="py-10 text-center text-muted-foreground">
                No cases match these filters.
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
      <CaseDrawer brief={open} onClose={() => setOpen(null)} />
    </>
  );
}

function CaseDrawer({ brief, onClose }: { brief: Brief | null; onClose: () => void }) {
  const q = useQuery({
    queryKey: ["result", brief?.id],
    enabled: !!brief,
    queryFn: async (): Promise<Detail> => {
      const res = await fetch(`/api/pg/v1/results/${brief!.id}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    },
  });
  const d = q.data;
  const p = (d?.payload ?? {}) as { scores?: ScoreRow[]; output?: OutputPayload; expected?: string | null; metadata?: Record<string, unknown> };
  const out = p.output ?? {};
  const prompt = (out.messages ?? []).filter((m) => m.role === "user" || m.role === "system");
  return (
    <Sheet open={!!brief} onOpenChange={(o) => !o && onClose()}>
      <SheetContent className="w-full gap-0 sm:max-w-2xl">
        <SheetHeader className="border-b">
          <SheetTitle className="flex flex-wrap items-center gap-2 font-mono text-sm">
            {brief?.case_id}
            {brief && (brief.status === "ok" || brief.status === "refused" ? <PassMark passed={brief.passed} /> : <Chip tone={STATUS_TONE[brief.status] ?? "muted"} dot={false}>{brief.status}</Chip>)}
            {brief?.critical && <Chip tone="warning" dot={false}>critical</Chip>}
          </SheetTitle>
          <SheetDescription>
            {brief?.suite} · repeat {brief?.repeat} · {ms(brief?.latency_ms)} · {usd(brief?.cost_usd)}
            {brief?.trace_id && (
              <>
                {" · "}
                <Link href={`/traces/${brief.trace_id}`} className="text-primary hover:underline">
                  trace
                </Link>
              </>
            )}
          </SheetDescription>
        </SheetHeader>
        <ScrollArea className="min-h-0 flex-1">
          <div className="grid gap-5 p-4 text-sm">
            {q.isLoading && (
              <>
                <Skeleton className="h-24" />
                <Skeleton className="h-32" />
              </>
            )}
            {q.isError && <p className="text-danger">Could not load this case.</p>}
            {d && (
              <>
                {out.error && <p className="rounded-md bg-danger/10 p-2 text-xs text-danger">{out.error}</p>}
                <Block title="Input">
                  {prompt.length ? prompt.map((m, i) => <Pre key={i} label={m.role}>{m.content}</Pre>) : <Pre>{d.input_preview}</Pre>}
                </Block>
                {(out.retrieved_context ?? []).length > 0 && (
                  <Block title={`Retrieved context (${out.retrieved_context!.length})`}>
                    <ol className="grid gap-1.5">
                      {out.retrieved_context!.map((c, i) => (
                        <li key={i} className="rounded-md bg-muted/60 p-2 text-xs text-muted-foreground">
                          <span className="mr-1.5 font-mono">doc{i}</span>
                          {c}
                        </li>
                      ))}
                    </ol>
                  </Block>
                )}
                <Block title={`Output${out.model_served ? ` · ${out.model_served}` : ""}`}>
                  <Pre>{out.text || "(empty)"}</Pre>
                  {(out.tool_calls ?? []).length > 0 && (
                    <div className="mt-2 grid gap-1">
                      {out.tool_calls!.map((t, i) => (
                        <p key={i} className="rounded-md bg-muted/60 px-2 py-1 font-mono text-xs">
                          {t.name}({JSON.stringify(t.arguments ?? {})})
                        </p>
                      ))}
                    </div>
                  )}
                </Block>
                {p.expected && (
                  <Block title="Expected">
                    <Pre>{p.expected}</Pre>
                  </Block>
                )}
                <Block title="Scores">
                  <div className="grid gap-2">
                    {(p.scores ?? []).map((s, i) => (
                      <div key={i} className="rounded-lg border p-2.5">
                        <div className="flex items-center justify-between gap-2">
                          <span className="font-medium">{metricInfo(s.metric).label}</span>
                          <span className="flex items-center gap-2">
                            <span className="tabular text-xs text-muted-foreground">{s.value.toFixed(2)}</span>
                            <PassMark passed={s.passed} />
                          </span>
                        </div>
                        {s.reason && <p className="mt-1 text-xs text-muted-foreground">{s.reason}</p>}
                        <p className="mt-1 font-mono text-[11px] text-muted-foreground/80">
                          {s.metric}
                          {s.judge_model ? ` · judge ${s.judge_model}` : ""}
                        </p>
                      </div>
                    ))}
                    {(p.scores ?? []).length === 0 && <p className="text-xs text-muted-foreground">No scores were recorded (the case did not complete).</p>}
                  </div>
                </Block>
              </>
            )}
          </div>
        </ScrollArea>
      </SheetContent>
    </Sheet>
  );
}

function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="grid gap-1.5">
      <h3 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">{title}</h3>
      {children}
    </section>
  );
}

function Pre({ children, label }: { children: React.ReactNode; label?: string }) {
  return (
    <div className="rounded-lg bg-muted/60 p-3">
      {label && <p className="mb-1 text-[11px] font-medium text-muted-foreground uppercase">{label}</p>}
      <pre className="font-sans text-sm leading-relaxed whitespace-pre-wrap [overflow-wrap:anywhere]">{children}</pre>
    </div>
  );
}
