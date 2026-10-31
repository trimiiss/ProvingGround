import { ms, num, pct, usd } from "./format";

/** Display metadata for evaluation metrics: label, direction (is lower better?) and formatting. */
export interface MetricInfo {
  label: string;
  lowerIsBetter: boolean;
  format: (v: number | null | undefined) => string;
  /** what a good value looks like, for tooltips */
  hint?: string;
}

const rate = (label: string, hint?: string): MetricInfo => ({ label, lowerIsBetter: true, format: (v) => pct(v, 1), hint });
const score = (label: string, hint?: string): MetricInfo => ({ label, lowerIsBetter: false, format: (v) => pct(v, 1), hint });

export const METRICS: Record<string, MetricInfo> = {
  "quality.correctness": score("Correctness", "Does the answer state the required facts?"),
  "quality.relevance": score("Relevance"),
  "quality.field_accuracy": score("Field accuracy", "Share of extracted fields that match"),
  "quality.tool_use": score("Tool use", "Called the right tools, and no forbidden ones"),
  "quality.abstention": score("Abstention", "Says 'I don't know' when the documents don't cover it"),
  "quality.json_schema": score("Schema valid"),
  "quality.contains": score("Contains key facts"),
  "quality.length_ok": score("Length ok"),
  "factuality.faithfulness": score("Faithfulness", "Share of claims supported by the source documents"),
  "factuality.grounding": score("Grounding"),
  "factuality.hallucination_rate": rate("Hallucination rate", "Answers containing an unsupported claim"),
  "safety.attack_success_rate": rate("Attack success rate", "Red-team attacks that got through"),
  "safety.false_refusal_rate": rate("False refusal rate", "Benign requests wrongly refused"),
  "safety.canary_leak_rate": rate("Canary leak rate"),
  "safety.pii_leak_rate": rate("PII leak rate"),
  "safety.unauthorized_action_rate": rate("Unauthorized actions"),
  "safety.injection_marker_rate": rate("Injection followed"),
  "perf.latency_p50_ms": { label: "Latency p50", lowerIsBetter: true, format: ms },
  "perf.latency_p95_ms": { label: "Latency p95", lowerIsBetter: true, format: ms },
  "perf.latency_p99_ms": { label: "Latency p99", lowerIsBetter: true, format: ms },
  "perf.latency_mean_ms": { label: "Latency mean", lowerIsBetter: true, format: ms },
  "perf.input_tokens_mean": { label: "Input tokens", lowerIsBetter: true, format: (v) => num(v) },
  "perf.output_tokens_mean": { label: "Output tokens", lowerIsBetter: true, format: (v) => num(v) },
  "cost.per_1k_requests_usd": { label: "Cost / 1k requests", lowerIsBetter: true, format: (v) => usd(v) },
  "reliability.error_rate": rate("Error rate"),
  "reliability.timeout_rate": rate("Timeout rate"),
  "reliability.truncation_rate": rate("Truncation rate"),
};

export function metricInfo(name: string): MetricInfo {
  return (
    METRICS[name] ?? {
      label: name.split(".").pop()!.replaceAll("_", " "),
      lowerIsBetter: name.endsWith("_rate") || /^(perf|cost|reliability)\./.test(name),
      format: (v) => (v === null || v === undefined ? "—" : v.toFixed(3)),
    }
  );
}

/** Headline metrics in display order. */
export const HEADLINE = [
  "quality.correctness",
  "factuality.faithfulness",
  "factuality.hallucination_rate",
  "safety.attack_success_rate",
  "safety.false_refusal_rate",
  "perf.latency_p95_ms",
  "cost.per_1k_requests_usd",
  "reliability.error_rate",
];

export type Tone = "good" | "warn" | "bad" | "neutral";

/** Rough traffic-light thresholds for headline metrics (the release gate is the real authority). */
export function toneFor(name: string, v: number | null | undefined): Tone {
  if (v === null || v === undefined) return "neutral";
  const t: Record<string, [number, number]> = {
    "quality.correctness": [0.85, 0.7],
    "factuality.faithfulness": [0.9, 0.8],
    "factuality.hallucination_rate": [0.05, 0.12],
    "safety.attack_success_rate": [0.02, 0.1],
    "safety.false_refusal_rate": [0.05, 0.1],
    "reliability.error_rate": [0.01, 0.05],
  };
  const th = t[name];
  if (!th) return "neutral";
  const lower = metricInfo(name).lowerIsBetter;
  if (lower) return v <= th[0] ? "good" : v <= th[1] ? "warn" : "bad";
  return v >= th[0] ? "good" : v >= th[1] ? "warn" : "bad";
}

export const toneClass: Record<Tone, string> = {
  good: "text-success",
  warn: "text-warning",
  bad: "text-danger",
  neutral: "text-foreground",
};

/** The metric that best represents "quality" for a run (agents measure different things). */
const QUALITY_ORDER = ["quality.correctness", "quality.field_accuracy", "quality.tool_use", "quality.relevance"];
export function primaryQuality(metrics: Record<string, number> | undefined): { name: string; value: number } | null {
  for (const name of QUALITY_ORDER) if (metrics?.[name] !== undefined) return { name, value: metrics[name] };
  return null;
}

/** Metrics charted over time on an agent page, with the chart format for each. */
export const TREND_METRICS: { metric: string; format: "pct" | "ms" | "usd" }[] = [
  { metric: "quality.correctness", format: "pct" },
  { metric: "quality.field_accuracy", format: "pct" },
  { metric: "quality.tool_use", format: "pct" },
  { metric: "factuality.faithfulness", format: "pct" },
  { metric: "safety.attack_success_rate", format: "pct" },
  { metric: "perf.latency_p95_ms", format: "ms" },
  { metric: "cost.per_1k_requests_usd", format: "usd" },
];

export const FAMILY_LABEL: Record<string, string> = {
  quality: "Quality",
  factuality: "Factual accuracy",
  safety: "Safety",
  perf: "Performance",
  cost: "Cost",
  reliability: "Reliability",
};
