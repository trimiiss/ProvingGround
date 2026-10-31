"use client";

import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, Loader2, Play, XCircle } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { JobBadge } from "@/components/status";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { components } from "@/lib/api-types";

type Job = components["schemas"]["JobOut"];

export interface LaunchAgent {
  slug: string;
  name: string;
  model: string | null;
  suites: string[];
}

const KINDS = [
  { value: "run", label: "Evaluation", help: "Run the agent's suites and store the results." },
  { value: "redteam", label: "Red-team campaign", help: "Attack the agent and report success rate by technique." },
  { value: "gate", label: "Release gate", help: "Compare a candidate model against today's configuration and decide pass/fail." },
] as const;

const MODELS = ["sim/model-a", "sim/model-b", "sim/model-c"];
const KEEP = "__keep__";

async function postJob(body: unknown): Promise<Job> {
  const res = await fetch("/api/pg/v1/jobs", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  if (!res.ok) throw new Error((await res.json().catch(() => ({ detail: res.statusText }))).detail ?? res.statusText);
  return res.json();
}

function ResultLinks({ job }: { job: Job }) {
  const r = (job.result ?? {}) as Record<string, string | number | null>;
  if (job.kind === "gate" && r.evaluation_id)
    return (
      <Link className="font-medium text-primary hover:underline" href={`/gates/${r.evaluation_id}`}>
        View gate verdict: {String(r.verdict)} →
      </Link>
    );
  if (r.run_id)
    return (
      <Link className="font-medium text-primary hover:underline" href={`/runs/${r.run_id}`}>
        Open run →
      </Link>
    );
  return null;
}

export function LaunchRun({
  agents,
  gates,
  agent,
  variant = "default",
  label = "Run evaluation",
}: {
  agents: LaunchAgent[];
  gates: string[];
  agent?: string;
  variant?: "default" | "outline";
  label?: string;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState<(typeof KINDS)[number]["value"]>("run");
  const [target, setTarget] = useState(agent ?? agents[0]?.slug ?? "");
  const [model, setModel] = useState(KEEP);
  const [sample, setSample] = useState("");
  const [policy, setPolicy] = useState(gates.includes("release") ? "release" : (gates[0] ?? "release"));
  const [jobId, setJobId] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const current = agents.find((a) => a.slug === target);
  const job = useQuery({
    queryKey: ["job", jobId],
    enabled: !!jobId,
    queryFn: async (): Promise<Job> => (await fetch(`/api/pg/v1/jobs/${jobId}`)).json(),
    refetchInterval: (q) => (q.state.data && ["succeeded", "failed"].includes(q.state.data.status) ? false : 900),
  });

  async function submit() {
    setSubmitting(true);
    try {
      const payload: Record<string, unknown> = { target };
      if (sample) payload.sample = Number(sample);
      if (model !== KEEP) {
        if (kind === "gate") payload.candidate_overrides = { model };
        else payload.overrides = { model };
      }
      if (kind === "gate") {
        payload.policy = policy;
        if (model === KEEP) throw new Error("Choose the candidate model to gate.");
      }
      const created = await postJob({ kind, payload });
      setJobId(created.id);
      router.refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not start the job");
    } finally {
      setSubmitting(false);
    }
  }

  const j = job.data;
  const done = j && ["succeeded", "failed"].includes(j.status);

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (!o) {
          setJobId(null);
          if (done) router.refresh();
        }
      }}
    >
      <DialogTrigger asChild>
        <Button variant={variant}>
          <Play className="size-4" />
          {label}
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{jobId ? "Running…" : label}</DialogTitle>
          <DialogDescription>{jobId ? "The worker picks this up immediately; results appear here when it finishes." : KINDS.find((k) => k.value === kind)?.help}</DialogDescription>
        </DialogHeader>

        {!jobId ? (
          <div className="grid gap-4">
            <div className="grid gap-1.5">
              <Label>What to run</Label>
              <div className="grid grid-cols-3 gap-1.5 rounded-lg bg-muted p-1">
                {KINDS.map((k) => (
                  <button
                    key={k.value}
                    type="button"
                    onClick={() => setKind(k.value)}
                    className={`rounded-md px-2 py-1.5 text-xs font-medium transition-colors ${kind === k.value ? "bg-background shadow-sm" : "text-muted-foreground hover:text-foreground"}`}
                  >
                    {k.label}
                  </button>
                ))}
              </div>
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="agent">Agent</Label>
              <Select value={target} onValueChange={setTarget} disabled={!!agent}>
                <SelectTrigger id="agent" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {agents.map((a) => (
                    <SelectItem key={a.slug} value={a.slug}>
                      {a.name} <span className="text-muted-foreground">· {a.model?.split("/").pop()}</span>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {current && <p className="text-xs text-muted-foreground">Suites: {current.suites.join(", ") || "none configured"}</p>}
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="grid gap-1.5">
                <Label htmlFor="model">{kind === "gate" ? "Candidate model" : "Model"}</Label>
                <Select value={model} onValueChange={setModel}>
                  <SelectTrigger id="model" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {kind !== "gate" && <SelectItem value={KEEP}>As configured ({current?.model?.split("/").pop() ?? "—"})</SelectItem>}
                    {kind === "gate" && <SelectItem value={KEEP}>Choose…</SelectItem>}
                    {MODELS.map((m) => (
                      <SelectItem key={m} value={m}>
                        {m}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor="sample">Cases per suite</Label>
                <Input id="sample" inputMode="numeric" placeholder="all" value={sample} onChange={(e) => setSample(e.target.value.replace(/\D/g, ""))} />
              </div>
            </div>
            {kind === "gate" && (
              <div className="grid gap-1.5">
                <Label htmlFor="policy">Gate policy</Label>
                <Select value={policy} onValueChange={setPolicy}>
                  <SelectTrigger id="policy" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {gates.map((g) => (
                      <SelectItem key={g} value={g}>
                        {g}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}
            <Button onClick={submit} disabled={submitting || !target}>
              {submitting && <Loader2 className="size-4 animate-spin" />}
              Start
            </Button>
          </div>
        ) : (
          <div className="grid gap-4">
            <div className="flex items-center justify-between">
              <span className="flex items-center gap-2 text-sm font-medium">
                {!j || !done ? <Loader2 className="size-4 animate-spin text-info" /> : j.status === "succeeded" ? <CheckCircle2 className="size-4 text-success" /> : <XCircle className="size-4 text-danger" />}
                {j?.kind ?? kind} · {target}
              </span>
              {j && <JobBadge status={j.status} />}
            </div>
            <Progress value={Math.round((j?.progress ?? (done ? 1 : 0)) * 100)} aria-label="Job progress" />
            {j?.error && <p className="rounded-md bg-danger/10 p-2 text-xs break-words text-danger">{j.error}</p>}
            {j?.status === "succeeded" && <ResultLinks job={j} />}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
