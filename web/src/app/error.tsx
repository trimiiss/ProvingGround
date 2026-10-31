"use client";

import { AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Page } from "@/components/page";

export default function ErrorPage({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <Page>
      <div className="mx-auto mt-16 flex max-w-lg flex-col items-center gap-3 text-center">
        <span className="grid size-12 place-items-center rounded-full bg-danger/12 text-danger">
          <AlertTriangle className="size-6" />
        </span>
        <h1 className="text-lg font-semibold">Something went wrong</h1>
        <p className="text-sm text-muted-foreground text-pretty">{error.message || "The page could not be loaded."}</p>
        <Button onClick={reset}>Try again</Button>
      </div>
    </Page>
  );
}
