import { Suspense } from "react";
import { Page, PageHeader, PageSkeleton, Section } from "@/components/page";
import { Card } from "@/components/ui/card";
import { UploadFolder } from "@/components/upload-folder";
import { getPacks } from "@/lib/api";

export const metadata = { title: "Upload agent" };

export default function UploadPage() {
  return (
    <Suspense fallback={<PageSkeleton rows={1} />}>
      <Upload />
    </Suspense>
  );
}

const FILES = [
  ["provingground.yaml", "Your bots (where to reach them, their secret code), which exams they take, and the pass/fail rules."],
  ["questions.jsonl", "One question per line, with the facts a right answer must contain."],
  ["attacks.jsonl (optional)", "Extra attacks of your own. Every bot already gets about 50 built-in ones."],
  ["bot.py (optional)", "Your bot's code, if it is a Python function rather than a URL."],
] as const;

const ATTACKS = [
  ["Stealing the hidden instructions", "Needs canaries: the secret code in your bot's instructions. Without one, these attacks are skipped."],
  ["Stealing a secret", "Needs the same canaries."],
  ["Stealing customer data", "Always runs. List your own support email or phone under public_info, or they count as leaks."],
  ["Talking about banned topics", "Needs forbidden_terms: the words your bot must never use. Without any, these attacks are skipped."],
] as const;

async function Upload() {
  const packs = await getPacks();
  return (
    <Page>
      <PageHeader
        title="Upload agent"
        description="Fill in the form or drop a test folder: your bot joins the fleet, takes its exams right away, and gets a pass or blocked verdict."
      />
      <UploadFolder packs={packs} />
      <Section title="What goes in the folder" description="Copy examples/upload-me and change it; drop the same project name again to replace it.">
        <Card className="divide-y p-0">
          {FILES.map(([name, what]) => (
            <div key={name} className="grid gap-1 px-4 py-3 sm:grid-cols-[14rem_1fr] sm:gap-4">
              <code className="text-sm font-medium">{name}</code>
              <p className="text-sm text-muted-foreground">{what}</p>
            </div>
          ))}
        </Card>
      </Section>
      <Section title="Built-in attacks" description="Run on every bot automatically. The results are on the Red team page.">
        <Card className="divide-y p-0">
          {ATTACKS.map(([name, needs]) => (
            <div key={name} className="grid gap-1 px-4 py-3 sm:grid-cols-[14rem_1fr] sm:gap-4">
              <p className="text-sm font-medium">{name}</p>
              <p className="text-sm text-muted-foreground">{needs}</p>
            </div>
          ))}
        </Card>
      </Section>
    </Page>
  );
}
