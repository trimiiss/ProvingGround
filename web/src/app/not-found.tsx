import Link from "next/link";
import { Page } from "@/components/page";
import { Button } from "@/components/ui/button";

export default function NotFound() {
  return (
    <Page>
      <div className="mx-auto mt-16 flex max-w-md flex-col items-center gap-3 text-center">
        <h1 className="text-lg font-semibold">Not found</h1>
        <p className="text-sm text-muted-foreground">That run, agent or trace does not exist (it may have been deleted).</p>
        <Button asChild>
          <Link href="/">Back to the fleet</Link>
        </Button>
      </div>
    </Page>
  );
}
