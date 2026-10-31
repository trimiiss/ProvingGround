import { RuleTable, VerdictBanner } from "@/components/gate-view";
import { Section } from "@/components/page";
import { Card } from "@/components/ui/card";
import { evaluateGate, orNull } from "@/lib/api";

/** Dry-run of the release gate for the compared runs (nothing is written to the gate history). */
export async function GateViewSection({ policy, candidate, baseline }: { policy: string; candidate: string; baseline: string }) {
  const result = await orNull(evaluateGate(policy, candidate, baseline));
  if (!result) return null;
  return (
    <Section title="Release decision" description={`The “${policy}” policy applied to these two runs. A dry run: it is not added to the gate history.`}>
      <VerdictBanner result={result} />
      <Card className="p-0">
        <RuleTable rules={result.rules} />
      </Card>
    </Section>
  );
}
