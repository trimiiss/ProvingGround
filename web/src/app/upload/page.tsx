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
  ["attacks.jsonl", "Messages that try to make the bot leak its secret or break its rules."],
  ["bot.py (optional)", "Your bot's code, if it is a Python function rather than a URL."],
] as const;

async function Upload() {
  const packs = await getPacks();
  return (
    <Page>
      <PageHeader
        title="Upload agent"
        description="Drop a test folder: its bots join the fleet, take their exams right away, and get a pass or blocked verdict. Drop it again after a fix to re-test."
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
    </Page>
  );
}
