"use client";

import { FilePlus2, Loader2 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";

/** Turns a production trace into a regression case in a dataset ("add to dataset"). */
export function AddToDataset({ traceId, observed }: { traceId: string; observed?: string | null }) {
  const [open, setOpen] = useState(false);
  const [dataset, setDataset] = useState("incidents");
  const [expected, setExpected] = useState("");
  const [critical, setCritical] = useState(true);
  const [busy, setBusy] = useState(false);
  const [added, setAdded] = useState<{ name: string; case_id: string } | null>(null);

  async function submit() {
    setBusy(true);
    try {
      const res = await fetch(`/api/pg/v1/traces/${traceId}/to-dataset`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ dataset: dataset.trim() || "incidents", expected: expected.trim() || null, critical, tags: [] }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail ?? res.statusText);
      setAdded({ name: dataset.trim() || "incidents", case_id: body.id ?? "" });
      toast.success("Added to the dataset");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not add the case");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (!o) setAdded(null);
      }}
    >
      <DialogTrigger asChild>
        <Button variant="outline">
          <FilePlus2 className="size-4" /> Add to dataset
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Turn this trace into a regression test</DialogTitle>
          <DialogDescription>The request becomes a test case. Add what a correct answer should say and every future run of this agent is checked against it.</DialogDescription>
        </DialogHeader>
        {added ? (
          <div className="grid gap-3 text-sm">
            <p>
              Added <span className="font-mono">{added.case_id}</span> to <span className="font-medium">{added.name}</span>.
            </p>
            <Link href={`/datasets/${added.name}`} className="font-medium text-primary hover:underline">
              Open the dataset →
            </Link>
          </div>
        ) : (
          <div className="grid gap-4">
            {observed && (
              <div className="rounded-lg bg-muted/60 p-3 text-xs">
                <p className="mb-1 font-medium text-muted-foreground uppercase">What the agent answered</p>
                <p className="line-clamp-4 whitespace-pre-wrap">{observed}</p>
              </div>
            )}
            <div className="grid gap-1.5">
              <Label htmlFor="expected">Correct answer (optional)</Label>
              <Textarea id="expected" rows={3} value={expected} onChange={(e) => setExpected(e.target.value)} placeholder="Key facts the answer must contain" />
            </div>
            <div className="grid grid-cols-[1fr_auto] items-end gap-4">
              <div className="grid gap-1.5">
                <Label htmlFor="dataset">Dataset</Label>
                <Input id="dataset" value={dataset} onChange={(e) => setDataset(e.target.value)} />
              </div>
              <label className="flex items-center gap-2 pb-2 text-sm">
                <Switch checked={critical} onCheckedChange={setCritical} aria-label="Critical case" />
                Critical
              </label>
            </div>
            <p className="text-xs text-muted-foreground">Critical cases must pass for a release to be approved.</p>
            <Button onClick={submit} disabled={busy}>
              {busy && <Loader2 className="size-4 animate-spin" />}
              Add case
            </Button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
