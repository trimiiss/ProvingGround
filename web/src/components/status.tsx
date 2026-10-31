import { cn } from "@/lib/utils";

type Tone = "success" | "warning" | "danger" | "info" | "muted";

const TONE: Record<Tone, { chip: string; dot: string }> = {
  success: { chip: "bg-success/12 text-success", dot: "bg-success" },
  warning: { chip: "bg-warning/15 text-warning", dot: "bg-warning" },
  danger: { chip: "bg-danger/12 text-danger", dot: "bg-danger" },
  info: { chip: "bg-info/12 text-info", dot: "bg-info" },
  muted: { chip: "bg-muted text-muted-foreground", dot: "bg-muted-foreground/50" },
};

export function Chip({ tone = "muted", children, dot = true, className }: { tone?: Tone; children: React.ReactNode; dot?: boolean; className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap", TONE[tone].chip, className)}>
      {dot && <span className={cn("size-1.5 rounded-full", TONE[tone].dot)} />}
      {children}
    </span>
  );
}

const HEALTH: Record<string, [Tone, string]> = {
  healthy: ["success", "Healthy"],
  warning: ["warning", "Warning"],
  failing: ["danger", "Failing"],
  unknown: ["muted", "No data"],
};
export const HealthBadge = ({ status }: { status: string }) => {
  const [tone, label] = HEALTH[status] ?? HEALTH.unknown;
  return <Chip tone={tone}>{label}</Chip>;
};

const VERDICT: Record<string, [Tone, string]> = {
  PASS: ["success", "Passed"],
  WARN: ["warning", "Warnings"],
  FAIL: ["danger", "Blocked"],
  INCONCLUSIVE: ["warning", "Inconclusive"],
  "N/A": ["muted", "N/A"],
};
export const VerdictBadge = ({ verdict }: { verdict: string }) => {
  const [tone, label] = VERDICT[verdict] ?? ["muted", verdict];
  return <Chip tone={tone}>{label}</Chip>;
};

const JOB: Record<string, Tone> = { queued: "muted", running: "info", succeeded: "success", failed: "danger" };
export const JobBadge = ({ status }: { status: string }) => <Chip tone={JOB[status] ?? "muted"}>{status}</Chip>;

const KIND: Record<string, Tone> = { eval: "info", redteam: "danger", inspect: "muted", online: "success" };
export const KindBadge = ({ kind }: { kind: string }) => (
  <Chip tone={KIND[kind] ?? "muted"} dot={false}>
    {kind === "redteam" ? "red team" : kind}
  </Chip>
);

export const PassMark = ({ passed }: { passed: boolean | null | undefined }) =>
  passed === null || passed === undefined ? (
    <Chip tone="muted" dot={false}>
      n/a
    </Chip>
  ) : passed ? (
    <Chip tone="success" dot={false}>
      pass
    </Chip>
  ) : (
    <Chip tone="danger" dot={false}>
      fail
    </Chip>
  );
