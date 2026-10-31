import { AlertTriangle, Download } from "lucide-react";
import Link from "next/link";
import { Suspense } from "react";
import { first, Pager, withParams } from "@/components/filters";
import { Page, PageHeader, PageSkeleton } from "@/components/page";
import { Chip } from "@/components/status";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { getDatasetCases, getDatasets, orNotFound } from "@/lib/api";
import { clip, num } from "@/lib/format";

const LIMIT = 30;

export async function generateMetadata(props: PageProps<"/datasets/[name]">) {
  return { title: (await props.params).name };
}

export default function DatasetPage(props: PageProps<"/datasets/[name]">) {
  return (
    <Suspense fallback={<PageSkeleton rows={1} />}>
      <Dataset params={props.params} searchParams={props.searchParams} />
    </Suspense>
  );
}

async function Dataset({ params, searchParams }: { params: PageProps<"/datasets/[name]">["params"]; searchParams: PageProps<"/datasets/[name]">["searchParams"] }) {
  const { name } = await params;
  const sp = await searchParams;
  const q = first(sp.q) ?? "";
  const tag = first(sp.tag) ?? "";
  const offset = Number(first(sp.offset) ?? 0) || 0;
  const [cases, all] = await Promise.all([orNotFound(getDatasetCases(name, { q: q || undefined, tag: tag || undefined, limit: LIMIT, offset })), getDatasets()]);
  const meta = all.find((d) => d.name === name);
  const base = `/datasets/${name}`;
  const cur = { q, tag };

  return (
    <Page>
      <PageHeader
        eyebrow={
          <Link href="/datasets" className="hover:underline">
            Datasets
          </Link>
        }
        title={name}
        description={`${meta?.description || "Test cases"} · ${num(meta?.n_cases ?? cases.total)} cases · version ${meta?.version ?? "—"}`}
        actions={
          <a href={`/api/pg/v1/datasets/${name}/export`} className="inline-flex h-9 items-center gap-1.5 rounded-lg border px-3 text-sm transition-colors hover:bg-muted">
            <Download className="size-4" /> Export JSONL
          </a>
        }
      />
      <form action={base} className="flex flex-wrap items-end gap-2">
        <Input name="q" defaultValue={q} placeholder="Search input, expected answer or metadata" className="max-w-md" aria-label="Search cases" />
        <Input name="tag" defaultValue={tag} placeholder="Tag" className="w-40" aria-label="Filter by tag" />
        <Button type="submit" variant="outline">
          Filter
        </Button>
        {(q || tag) && (
          <Link href={base} className="text-sm text-muted-foreground hover:underline">
            Clear
          </Link>
        )}
      </form>
      <Card className="p-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Case</TableHead>
              <TableHead>Input</TableHead>
              <TableHead>Expected</TableHead>
              <TableHead>Tags</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {cases.items.map((c) => (
              <TableRow key={c.id}>
                <TableCell className="max-w-56">
                  <p className="flex items-center gap-1 truncate font-mono text-xs">
                    {c.critical && <AlertTriangle className="size-3.5 shrink-0 text-warning" aria-label="critical case" />}
                    <span className="truncate">{c.id}</span>
                  </p>
                  {c.context && c.context.length > 0 && <p className="text-xs text-muted-foreground">{c.context.length} context docs</p>}
                </TableCell>
                <TableCell className="max-w-96">{clip(c.input, 160)}</TableCell>
                <TableCell className="max-w-72 text-muted-foreground">{clip(c.expected, 120) || "—"}</TableCell>
                <TableCell>
                  <span className="flex flex-wrap gap-1">
                    {(c.tags ?? []).slice(0, 4).map((t) => (
                      <Link key={t} href={withParams(base, cur, { tag: t, offset: 0 })}>
                        <Chip tone="muted" dot={false}>{t}</Chip>
                      </Link>
                    ))}
                  </span>
                </TableCell>
              </TableRow>
            ))}
            {cases.items.length === 0 && (
              <TableRow>
                <TableCell colSpan={4} className="py-10 text-center text-muted-foreground">
                  No cases match.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </Card>
      <Pager total={cases.total} limit={LIMIT} offset={offset} href={(o) => withParams(base, cur, { offset: o })} />
    </Page>
  );
}
