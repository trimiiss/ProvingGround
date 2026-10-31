import { Suspense } from "react";
import { FilterChips, first, Pager, withParams } from "@/components/filters";
import { Field, NativeSelect } from "@/components/native-select";
import { Page, PageHeader, PageSkeleton } from "@/components/page";
import { RunsTable } from "@/components/runs-table";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { getOverview, getRuns } from "@/lib/api";

export const metadata = { title: "Runs" };

const LIMIT = 25;

export default function RunsPage(props: PageProps<"/runs">) {
  return (
    <Suspense fallback={<PageSkeleton rows={1} />}>
      <Runs searchParams={props.searchParams} />
    </Suspense>
  );
}

async function Runs({ searchParams }: { searchParams: PageProps<"/runs">["searchParams"] }) {
  const sp = await searchParams;
  const agent = first(sp.agent) ?? "all";
  const kind = first(sp.kind) ?? "all";
  const offset = Number(first(sp.offset) ?? 0) || 0;
  const [runs, overview] = await Promise.all([
    getRuns({ agent: agent === "all" ? undefined : agent, kind: kind === "all" ? undefined : kind, limit: LIMIT, offset }),
    getOverview(),
  ]);
  const cur = { agent, kind };
  const slugs = overview.agents.map((a) => a.agent.slug).sort();

  return (
    <Page>
      <PageHeader title="Runs" description="Every evaluation, red-team campaign and imported benchmark, newest first." />
      <div className="flex flex-wrap items-end justify-between gap-4">
        <form className="flex items-end gap-2" action="/runs">
          {kind !== "all" && <input type="hidden" name="kind" value={kind} />}
          <Field label="Agent" className="w-56">
            <NativeSelect name="agent" defaultValue={agent}>
              <option value="all">All agents</option>
              {slugs.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </NativeSelect>
          </Field>
          <Button type="submit" variant="outline">
            Apply
          </Button>
        </form>
        <FilterChips
          current={kind}
          options={[{ value: "all" }, { value: "eval" }, { value: "redteam", label: "red team" }, { value: "inspect" }]}
          href={(v) => withParams("/runs", cur, { kind: v })}
        />
      </div>
      <Card className="p-0">
        <RunsTable runs={runs.items} />
      </Card>
      <Pager total={runs.total} limit={LIMIT} offset={offset} href={(o) => withParams("/runs", cur, { offset: o })} />
    </Page>
  );
}
