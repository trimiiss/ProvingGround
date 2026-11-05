"use client";

import { useQueries } from "@tanstack/react-query";
import { CheckCircle2, FolderUp, Loader2, RotateCcw, Trash2, XCircle } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { AddBotForm } from "@/components/add-bot-form";
import { JobBadge, VerdictBadge } from "@/components/status";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import type { components } from "@/lib/api-types";
import { cn } from "@/lib/utils";

type Job = components["schemas"]["JobOut"];
type Pack = components["schemas"]["PackOut"];

// The same rules the server applies: only the text files a test folder needs, no caches or results.
const KEEP = /\.(ya?ml|jsonl|json|py|md|txt|csv)$/i;
const MAX_BYTES = 5_000_000;
const DONE = ["succeeded", "failed"];

interface Picked {
  path: string;
  file: File;
}

const hidden = (name: string) => name.startsWith(".") || name === "__pycache__" || name === "node_modules";

async function walk(entry: FileSystemEntry, out: Picked[]): Promise<void> {
  if (entry.isFile) {
    const file = await new Promise<File>((resolve, reject) => (entry as FileSystemFileEntry).file(resolve, reject));
    out.push({ path: entry.fullPath.replace(/^\//, ""), file });
    return;
  }
  const reader = (entry as FileSystemDirectoryEntry).createReader();
  for (;;) {
    const batch = await new Promise<FileSystemEntry[]>((resolve, reject) => reader.readEntries(resolve, reject));
    if (!batch.length) return;
    for (const child of batch) if (!hidden(child.name)) await walk(child, out);
  }
}

async function readFolder(picked: Picked[]): Promise<Record<string, string>> {
  const kept = picked.filter((p) => KEEP.test(p.path) && !p.path.split("/").some(hidden));
  if (!kept.length) throw new Error("No test files here: the folder needs a provingground.yaml and its .jsonl files.");
  if (kept.reduce((n, p) => n + p.file.size, 0) > MAX_BYTES)
    throw new Error("This folder is larger than 5 MB. Drop just the test folder, not a whole project.");
  return Object.fromEntries(await Promise.all(kept.map(async (p) => [p.path, await p.file.text()] as const)));
}

async function send<T>(url: string, init: RequestInit): Promise<T> {
  const res = await fetch(url, { ...init, headers: { "content-type": "application/json" } });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = body?.detail;
    throw new Error(typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map((d) => d.msg).join("; ") : res.statusText);
  }
  return body as T;
}

interface Tracking {
  name: string;
  jobs: Job[];
}

export function UploadFolder({ packs }: { packs: Pack[] }) {
  const router = useRouter();
  const input = useRef<HTMLInputElement | null>(null);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tracking, setTracking] = useState<Tracking | null>(null);

  const jobs = useQueries({
    queries: (tracking?.jobs ?? []).map((j) => ({
      queryKey: ["job", j.id],
      queryFn: async (): Promise<Job> => (await fetch(`/api/pg/v1/jobs/${j.id}`)).json(),
      initialData: j,
      refetchInterval: (q: { state: { data?: Job } }) => (DONE.includes(q.state.data?.status ?? "") ? false : 900),
    })),
  });
  const allDone = jobs.length > 0 && jobs.every((q) => DONE.includes(q.data?.status ?? ""));
  useEffect(() => {
    if (allDone) router.refresh(); // the fleet and the folder list now include the new results
  }, [allDone, router]);

  async function upload(collect: () => Promise<Picked[]>) {
    setBusy(true);
    setError(null);
    try {
      const files = await readFolder(await collect());
      const pack = await send<components["schemas"]["PackUploaded"]>("/api/pg/v1/packs", { method: "POST", body: JSON.stringify({ files }) });
      setTracking({ name: pack.name, jobs: pack.jobs });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed");
    } finally {
      setBusy(false);
    }
  }

  async function addBot(files: Record<string, string>) {
    const pack = await send<components["schemas"]["PackUploaded"]>("/api/pg/v1/packs", { method: "POST", body: JSON.stringify({ files }) });
    setTracking({ name: pack.name, jobs: pack.jobs });
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragging(false);
    // Entries must be taken during the event: the browser empties the drop data afterwards.
    const entries = [...e.dataTransfer.items].map((i) => i.webkitGetAsEntry()).filter((x): x is FileSystemEntry => !!x);
    const flat = [...e.dataTransfer.files];
    void upload(async () => {
      if (!entries.length) return flat.map((file) => ({ path: file.name, file }));
      const out: Picked[] = [];
      for (const entry of entries) await walk(entry, out);
      return out;
    });
  }

  async function retest(pack: Pack) {
    try {
      const started = await Promise.all(
        pack.agents.map((target) =>
          send<Job>("/api/pg/v1/jobs", { method: "POST", body: JSON.stringify({ kind: "run", payload: { target, ...(pack.gate ? { gate: pack.gate } : {}) } }) }),
        ),
      );
      setTracking({ name: pack.name, jobs: started });
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not start the tests");
    }
  }

  return (
    <div className="grid gap-6">
      {tracking && (
        <section className="grid gap-3" aria-label="Test results">
          <h2 className="text-sm font-semibold tracking-tight">
            {allDone ? "Results" : "Testing"} · <span className="font-mono">{tracking.name}</span>
          </h2>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {jobs.map((q, i) => (
              <JobCard key={tracking.jobs[i].id} job={q.data ?? tracking.jobs[i]} />
            ))}
          </div>
        </section>
      )}

      <AddBotForm onSubmit={addBot} />

      <h2 className="text-sm font-semibold tracking-tight">Or drop a test folder</h2>
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={cn(
          "flex flex-col items-center gap-3 rounded-xl border-2 border-dashed px-6 py-12 text-center transition-colors",
          dragging ? "border-primary bg-primary/5" : "border-border",
        )}
      >
        {busy ? <Loader2 className="size-9 animate-spin text-primary" /> : <FolderUp className="size-9 text-muted-foreground" />}
        <div>
          <p className="font-medium">{busy ? "Uploading…" : "Drop a test folder here"}</p>
          <p className="mt-1 text-sm text-muted-foreground">
            It needs a <code>provingground.yaml</code> at the top. To try it, drop <code>examples/upload-me</code> from the repository.
          </p>
        </div>
        <Button variant="outline" disabled={busy} onClick={() => input.current?.click()}>
          Choose a folder
        </Button>
        <input
          ref={(el) => {
            input.current = el;
            el?.setAttribute("webkitdirectory", "");
          }}
          type="file"
          multiple
          className="hidden"
          aria-label="Choose a test folder"
          data-testid="folder-input"
          onChange={(e) => {
            const chosen = [...(e.target.files ?? [])];
            e.target.value = "";
            void upload(async () => chosen.map((file) => ({ path: file.webkitRelativePath || file.name, file })));
          }}
        />
      </div>

      {error && (
        <p role="alert" className="rounded-lg border border-danger/30 bg-danger/10 p-3 text-sm break-words text-danger">
          {error}
        </p>
      )}

      <section className="grid gap-3">
        <h2 className="text-sm font-semibold tracking-tight">Uploaded folders ({packs.length})</h2>
        {packs.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nothing uploaded yet.</p>
        ) : (
          <Card className="divide-y p-0">
            {packs.map((p) => (
              <PackRow key={p.name} pack={p} onRetest={() => retest(p)} />
            ))}
          </Card>
        )}
      </section>
    </div>
  );
}

function JobCard({ job }: { job: Job }) {
  const target = String(job.payload?.target ?? "");
  const r = (job.result ?? {}) as Record<string, string | number | null>;
  const done = DONE.includes(job.status);
  return (
    <Card className="gap-3 p-4">
      <div className="flex items-center justify-between gap-2">
        <span className="flex min-w-0 items-center gap-2 font-medium">
          {!done ? <Loader2 className="size-4 shrink-0 animate-spin text-info" /> : job.status === "succeeded" ? <CheckCircle2 className="size-4 shrink-0 text-success" /> : <XCircle className="size-4 shrink-0 text-danger" />}
          <Link href={`/agents/${target}`} className="truncate hover:underline">
            {target}
          </Link>
        </span>
        {r.verdict ? <VerdictBadge verdict={String(r.verdict)} /> : <JobBadge status={job.status} />}
      </div>
      {!done && <Progress value={Math.round((job.progress ?? 0) * 100)} aria-label={`${target} progress`} />}
      {job.error && <p className="rounded-md bg-danger/10 p-2 text-xs break-words text-danger">{job.error}</p>}
      {r.run_id && (
        <p className="text-sm text-muted-foreground">
          {typeof r.pass_rate === "number" ? `${Math.round(r.pass_rate * 100)}% of checks passed` : "Finished"}
        </p>
      )}
      {r.run_id && (
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
          <Link className="font-medium text-primary hover:underline" href={`/runs/${r.run_id}`}>
            Answers and grades →
          </Link>
          {r.evaluation_id && (
            <Link className="font-medium text-primary hover:underline" href={`/gates/${r.evaluation_id}`}>
              Why {r.verdict === "PASS" ? "it passed" : "it was blocked"} →
            </Link>
          )}
        </div>
      )}
    </Card>
  );
}

function PackRow({ pack, onRetest }: { pack: Pack; onRetest: () => void }) {
  const router = useRouter();
  const [confirming, setConfirming] = useState(false);

  async function remove() {
    try {
      const res = await fetch(`/api/pg/v1/packs/${encodeURIComponent(pack.name)}`, { method: "DELETE" });
      if (!res.ok && res.status !== 404) throw new Error(res.statusText);
      toast.success(`Removed ${pack.name}`);
      router.refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not remove it");
    }
  }

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
      <div className="min-w-0">
        <p className="font-mono text-sm font-medium">{pack.name}</p>
        <p className="text-xs text-muted-foreground">
          {pack.agents.length} bot{pack.agents.length === 1 ? "" : "s"}:{" "}
          {pack.agents.map((a, i) => (
            <span key={a}>
              {i > 0 && ", "}
              <Link href={`/agents/${a}`} className="hover:underline">
                {a}
              </Link>
            </span>
          ))}{" "}
          · {pack.files.length} files
        </p>
      </div>
      <div className="flex items-center gap-2">
        <Button variant="outline" size="sm" onClick={onRetest} disabled={!pack.agents.length}>
          <RotateCcw className="size-3.5" />
          Test again
        </Button>
        {confirming ? (
          <>
            <Button variant="destructive" size="sm" onClick={remove}>
              Remove {pack.agents.length} bot{pack.agents.length === 1 ? "" : "s"}
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
          </>
        ) : (
          <Button variant="ghost" size="sm" onClick={() => setConfirming(true)} aria-label={`Remove ${pack.name}`}>
            <Trash2 className="size-3.5" />
          </Button>
        )}
      </div>
    </div>
  );
}
