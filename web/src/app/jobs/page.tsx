import Link from "next/link";
import { Suspense } from "react";
import { AutoRefresh } from "@/components/auto-refresh";
import { FilterChips, first, Pager, withParams } from "@/components/filters";
import { Empty, Page, PageHeader, PageSkeleton, Section } from "@/components/page";
import { Chip, JobBadge } from "@/components/status";
import { Card } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { type JobOut, getJobs, getSchedules } from "@/lib/api";
import { dateTime, ms, relTime } from "@/lib/format";

export const metadata = { title: "Jobs" };

const LIMIT = 25;

export default function JobsPage(props: PageProps<"/jobs">) {
  return (
    <Suspense fallback={<PageSkeleton rows={2} />}>
      <Jobs searchParams={props.searchParams} />
    </Suspense>
  );
}

function target(j: JobOut): string {
  const p = j.payload as { target?: string };
  return p.target ?? "—";
}

function link(j: JobOut) {
  const r = (j.result ?? {}) as Record<string, string | number | null>;
  if (j.kind === "gate" && r.evaluation_id) return { href: `/gates/${r.evaluation_id}`, label: `gate ${String(r.verdict)}` };
  if (r.run_id) return { href: `/runs/${r.run_id}`, label: "open run" };
  return null;
}

async function Jobs({ searchParams }: { searchParams: PageProps<"/jobs">["searchParams"] }) {
  const sp = await searchParams;
  const status = first(sp.status) ?? "all";
  const offset = Number(first(sp.offset) ?? 0) || 0;
  const [jobs, schedules] = await Promise.all([getJobs({ status: status === "all" ? undefined : status, limit: LIMIT, offset }), getSchedules()]);
  const active = jobs.items.some((j) => j.status === "queued" || j.status === "running");

  return (
    <Page>
      {active && <AutoRefresh seconds={3} />}
      <PageHeader title="Jobs" description="Evaluations, red-team campaigns and gate checks run by the worker, whether you launched them here, from CI, or on a schedule." />

      <Section
        title={`Queue (${jobs.total})`}
        actions={<FilterChips current={status} options={[{ value: "all" }, { value: "queued" }, { value: "running" }, { value: "succeeded" }, { value: "failed" }]} href={(v) => withParams("/jobs", {}, { status: v })} />}
      >
        {jobs.items.length === 0 ? (
          <Empty title="No jobs" />
        ) : (
          <Card className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Job</TableHead>
                  <TableHead>Target</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="w-48">Progress</TableHead>
                  <TableHead>Result</TableHead>
                  <TableHead className="text-right">Duration</TableHead>
                  <TableHead className="text-right">Created</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {jobs.items.map((j) => {
                  const l = link(j);
                  const dur = j.started_at && j.finished_at ? Date.parse(j.finished_at) - Date.parse(j.started_at) : null;
                  return (
                    <TableRow key={j.id}>
                      <TableCell>
                        <Chip tone="muted" dot={false}>{j.kind}</Chip>
                        <p className="mt-0.5 font-mono text-[11px] text-muted-foreground">{j.id.slice(0, 8)}</p>
                      </TableCell>
                      <TableCell>{target(j)}</TableCell>
                      <TableCell>
                        <JobBadge status={j.status} />
                        {j.attempts > 1 && <span className="ml-1.5 text-xs text-muted-foreground">attempt {j.attempts}</span>}
                      </TableCell>
                      <TableCell>
                        <Progress value={Math.round((j.progress ?? (j.status === "succeeded" ? 1 : 0)) * 100)} aria-label="Job progress" />
                      </TableCell>
                      <TableCell className="max-w-72">
                        {j.error ? (
                          <span className="line-clamp-2 text-xs text-danger" title={j.error}>
                            {j.error}
                          </span>
                        ) : l ? (
                          <Link href={l.href} className="text-primary hover:underline">
                            {l.label}
                          </Link>
                        ) : (
                          "—"
                        )}
                      </TableCell>
                      <TableCell className="tabular text-right text-muted-foreground">{dur === null ? "—" : ms(dur)}</TableCell>
                      <TableCell className="text-right text-muted-foreground" title={j.created_at}>
                        {relTime(j.created_at)}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </Card>
        )}
        <Pager total={jobs.total} limit={LIMIT} offset={offset} href={(o) => withParams("/jobs", { status }, { offset: o })} />
      </Section>

      <Section title="Schedules" description="Recurring jobs, in UTC. Nightly full-suite runs catch silent model drift.">
        {schedules.length === 0 ? (
          <Empty title="No schedules yet">Create one with <code>PUT /v1/schedules/&#123;name&#125;</code>.</Empty>
        ) : (
          <Card className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Cron</TableHead>
                  <TableHead>Runs</TableHead>
                  <TableHead>Last run</TableHead>
                  <TableHead>Next run</TableHead>
                  <TableHead className="text-right">State</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {schedules.map((s) => (
                  <TableRow key={s.id}>
                    <TableCell className="font-medium">{s.name}</TableCell>
                    <TableCell className="font-mono text-xs">{s.cron}</TableCell>
                    <TableCell>
                      {s.job_kind} <span className="text-muted-foreground">{(s.payload as { target?: string })?.target ?? "all agents"}</span>
                    </TableCell>
                    <TableCell className="text-muted-foreground">{s.last_run_at ? dateTime(s.last_run_at) : "never"}</TableCell>
                    <TableCell className="text-muted-foreground">{s.next_run_at ? dateTime(s.next_run_at) : "—"}</TableCell>
                    <TableCell className="text-right">
                      <Chip tone={s.enabled ? "success" : "muted"}>{s.enabled ? "enabled" : "paused"}</Chip>
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
