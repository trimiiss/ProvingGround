import { GitCompareArrows } from "lucide-react";
import Link from "next/link";
import { Suspense } from "react";
import { RuleTable, VerdictBanner } from "@/components/gate-view";
import { Page, PageHeader, PageSkeleton, Section } from "@/components/page";
import { Card } from "@/components/ui/card";
import { api, type GateEvaluation, type GateResult, orNotFound } from "@/lib/api";
import { dateTime, shortId } from "@/lib/format";

export async function generateMetadata(props: PageProps<"/gates/[id]">) {
  return { title: `Gate #${(await props.params).id}` };
}

export default function GatePage(props: PageProps<"/gates/[id]">) {
  return (
    <Suspense fallback={<PageSkeleton rows={2} />}>
      <GateDetail params={props.params} />
    </Suspense>
  );
}

async function GateDetail({ params }: { params: PageProps<"/gates/[id]">["params"] }) {
  const { id } = await params;
  const evaluation = await orNotFound(api<GateEvaluation>(`/v1/gates/evaluations/${encodeURIComponent(id)}`));
  const result = evaluation.result as unknown as GateResult;
  const blocking = result.rules.filter((r) => r.severity === "block");
  const passed = blocking.filter((r) => r.verdict === "PASS").length;

  return (
    <Page>
      <PageHeader
        eyebrow={
          <Link href="/gates" className="hover:underline">
            Quality gates
          </Link>
        }
        title={`${evaluation.agent} · ${evaluation.policy}`}
        description={`Decided ${dateTime(evaluation.created_at)} · ${passed} of ${blocking.length} blocking rules passed`}
        actions={
          evaluation.baseline_run_id && (
            <Link href={`/compare?baseline=${evaluation.baseline_run_id}&candidate=${evaluation.candidate_run_id}&policy=${evaluation.policy}`} className="inline-flex h-9 items-center gap-1.5 rounded-lg border px-3 text-sm transition-colors hover:bg-muted">
              <GitCompareArrows className="size-4" /> Full comparison
            </Link>
          )
        }
      />
      <VerdictBanner result={result} />
      <Section title="Rules" description="Baseline is the version in production; candidate is the change under test.">
        <Card className="p-0">
          <RuleTable rules={result.rules} />
        </Card>
      </Section>
      <div className="grid gap-4 md:grid-cols-2">
        <Section title="Runs compared">
          <Card className="gap-1.5 p-4 text-sm">
            <p className="flex justify-between">
              <span className="text-muted-foreground">Candidate</span>
              <Link href={`/runs/${evaluation.candidate_run_id}`} className="font-mono text-primary hover:underline">
                {shortId(evaluation.candidate_run_id)}
              </Link>
            </p>
            <p className="flex justify-between">
              <span className="text-muted-foreground">Baseline</span>
              {evaluation.baseline_run_id ? (
                <Link href={`/runs/${evaluation.baseline_run_id}`} className="font-mono text-primary hover:underline">
                  {shortId(evaluation.baseline_run_id)}
                </Link>
              ) : (
                "none (absolute thresholds only)"
              )}
            </p>
          </Card>
        </Section>
        {result.notes.length > 0 && (
          <Section title="Notes">
            <Card className="gap-1 p-4 text-sm text-muted-foreground">
              {result.notes.map((n) => (
                <p key={n}>{n}</p>
              ))}
            </Card>
          </Section>
        )}
      </div>
    </Page>
  );
}
