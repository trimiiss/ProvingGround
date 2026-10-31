import { Download } from "lucide-react";
import Link from "next/link";
import { Suspense } from "react";
import { Empty, Page, PageHeader, PageSkeleton } from "@/components/page";
import { Chip } from "@/components/status";
import { Card } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { getDatasets } from "@/lib/api";
import { num } from "@/lib/format";

export const metadata = { title: "Datasets" };

export default function DatasetsPage() {
  return (
    <Suspense fallback={<PageSkeleton rows={1} />}>
      <Datasets />
    </Suspense>
  );
}

async function Datasets() {
  const datasets = (await getDatasets()).sort((a, b) => a.name.localeCompare(b.name));
  return (
    <Page>
      <PageHeader title="Datasets" description="Test cases an agent is evaluated on. The version is a hash of the content, so a run always says exactly which cases it saw. The “incidents” dataset collects production failures turned into tests." />
      {datasets.length === 0 ? (
        <Empty title="No datasets yet">Run <code>pground push</code> or seed the platform.</Empty>
      ) : (
        <Card className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Dataset</TableHead>
                <TableHead>Description</TableHead>
                <TableHead>Source</TableHead>
                <TableHead>Version</TableHead>
                <TableHead className="text-right">Cases</TableHead>
                <TableHead className="w-12" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {datasets.map((d) => (
                <TableRow key={d.name}>
                  <TableCell>
                    <Link href={`/datasets/${d.name}`} className="font-medium text-primary hover:underline">
                      {d.name}
                    </Link>
                  </TableCell>
                  <TableCell className="max-w-96 text-muted-foreground">{d.description || "—"}</TableCell>
                  <TableCell>
                    <Chip tone={d.source === "incident" || d.name === "incidents" ? "warning" : "muted"} dot={false}>
                      {d.source}
                    </Chip>
                  </TableCell>
                  <TableCell className="font-mono text-xs text-muted-foreground">{d.version}</TableCell>
                  <TableCell className="tabular text-right">{num(d.n_cases)}</TableCell>
                  <TableCell>
                    <a href={`/api/pg/v1/datasets/${d.name}/export`} aria-label={`Download ${d.name} as JSONL`} className="inline-flex size-8 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground">
                      <Download className="size-4" />
                    </a>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      )}
    </Page>
  );
}
