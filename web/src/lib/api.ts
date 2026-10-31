import "server-only";

import { notFound } from "next/navigation";
import type { components } from "./api-types";

/** Server-side client for the ProvingGround API. The admin key lives here and never reaches the browser. */

const BASE = (process.env.PGROUND_API_URL ?? "http://localhost:8000").replace(/\/$/, "");
const KEY = process.env.PGROUND_API_KEY ?? "dev-admin-key";

type S = components["schemas"];
export type Overview = S["Overview"];
export type AgentHealth = S["AgentHealth"];
export type RunBrief = S["RunBrief"];
export type RunDetail = S["RunDetail"];
export type ResultBrief = S["ResultBrief"];
export type ResultDetail = S["ResultDetail"];
export type Comparison = S["Comparison"];
export type MetricDelta = S["MetricDelta"];
export type GateResult = S["GateResult"];
export type GateBrief = S["GateBrief"];
export type GateEvaluation = S["GateEvaluationOut"];
export type GatePolicy = S["GatePolicy"];
export type TrendPoint = S["TrendPoint"];
export type TrafficBucket = S["TrafficBucket"];
export type TraceBrief = S["TraceBrief"];
export type TraceDetail = S["TraceDetail"];
export type SpanOut = S["SpanOut"];
export type JobOut = S["JobOut"];
export type DatasetOut = S["DatasetOut"];
export type ModelStats = S["ModelStats"];
export type ConfigOut = S["ConfigOut"];
export type ScheduleOut = S["ScheduleOut"];

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      ...init,
      headers: { "x-api-key": KEY, ...(init.body ? { "content-type": "application/json" } : {}), ...init.headers },
      cache: "no-store",
    });
  } catch {
    throw new ApiError(
      `Cannot reach the ProvingGround API at ${BASE}. Start the stack with \`docker compose up -d\`.`,
      503,
    );
  }
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new ApiError(`API ${res.status} on ${path}: ${detail.slice(0, 300)}`, res.status);
  }
  return res.json() as Promise<T>;
}

/** Resolve a request, turning a 404 from the API into the Next.js not-found page. */
export async function orNotFound<T>(request: Promise<T>): Promise<T> {
  try {
    return await request;
  } catch (e) {
    if (e instanceof ApiError && (e.status === 404 || e.status === 422)) notFound();
    throw e;
  }
}

/** Resolve a request, or null when the API has nothing for it. */
export async function orNull<T>(request: Promise<T>): Promise<T | null> {
  try {
    return await request;
  } catch (e) {
    if (e instanceof ApiError && e.status < 500) return null;
    throw e;
  }
}

type Query = Record<string, string | number | boolean | null | undefined>;
export function qs(params: Query): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") sp.set(k, String(v));
  const s = sp.toString();
  return s ? `?${s}` : "";
}

export interface Page<T> {
  items: T[];
  total: number;
}

export const getOverview = () => api<Overview>("/v1/overview");
export const getAgent = (slug: string) => api<AgentHealth>(`/v1/agents/${slug}`);
export const getAgentTrend = (slug: string, metric: string, days = 30) =>
  api<TrendPoint[]>(`/v1/agents/${slug}/trend${qs({ metric, days })}`);
export const getAgentTraffic = (slug: string, hours = 24, bucket_min = 60) =>
  api<TrafficBucket[]>(`/v1/agents/${slug}/traffic${qs({ hours, bucket_min })}`);
export const getFleetTraffic = (hours = 24, bucket_min = 60) =>
  api<TrafficBucket[]>(`/v1/traffic${qs({ hours, bucket_min })}`);
export const getRuns = (q: Query = {}) => api<Page<RunBrief>>(`/v1/runs${qs(q)}`);
export const getRun = (id: string) => api<RunDetail>(`/v1/runs/${id}`);
export const getResults = (id: string, q: Query = {}) => api<Page<ResultBrief>>(`/v1/runs/${id}/results${qs(q)}`);
export const getRedteam = (id: string) => api<RedteamSummary>(`/v1/runs/${id}/redteam`);
export const getCompare = (baseline: string, candidate: string) =>
  api<Comparison>(`/v1/compare${qs({ baseline, candidate })}`);
export const getGateEvaluations = (q: Query = {}) => api<GateEvaluation[]>(`/v1/gates/evaluations${qs(q)}`);
export const getGatePolicies = () => api<Record<string, GatePolicy>>("/v1/gates/policies");
export const getTraces = (q: Query = {}) => api<TraceBrief[]>(`/v1/traces${qs(q)}`);
export const getTrace = (id: string) => api<TraceDetail>(`/v1/traces/${id}`);
export const getDatasets = () => api<DatasetOut[]>("/v1/datasets");
export const getDatasetCases = (name: string, q: Query = {}) =>
  api<{ items: DatasetCase[]; total: number }>(`/v1/datasets/${name}/cases${qs(q)}`);
export const getModels = (days = 30) => api<ModelStats[]>(`/v1/models${qs({ days })}`);
export const getJobs = (q: Query = {}) => api<Page<JobOut>>(`/v1/jobs${qs(q)}`);
export const getSchedules = () => api<ScheduleOut[]>("/v1/schedules");
export const getConfig = () => api<ConfigOut>("/v1/config");

/** Red-team summary (the API returns a plain dict, so it is typed here). */
export interface AttackCell {
  attempts: number;
  successes: number;
}
export interface RedteamSummary {
  target: string;
  model: string | null;
  run_id: string;
  attempts: number;
  successes: number;
  asr: number | null;
  asr_ci: [number | null, number | null];
  by_goal: Record<string, AttackCell>;
  by_technique: Record<string, AttackCell>;
  by_converter: Record<string, AttackCell>;
  grid: Record<string, Record<string, AttackCell>>;
  false_refusal_rate: number | null;
  not_scored: number;
  top_successes: {
    case_id: string;
    goal: string;
    technique: string;
    converter: string;
    prompt: string;
    response: string;
    evidence: string[];
  }[];
}

export interface DatasetCase {
  id: string;
  input?: string;
  expected?: string;
  context?: string[];
  tags?: string[];
  critical?: boolean;
  metadata?: Record<string, unknown>;
}

/** Evaluate a candidate run against a gate policy without writing to the gate history. */
export const evaluateGate = (policy: string, candidate: string, baseline: string | null) =>
  api<GateResult>("/v1/gates/evaluate?record=false", {
    method: "POST",
    body: JSON.stringify({ policy, candidate_run_id: candidate, baseline_run_id: baseline }),
  });
