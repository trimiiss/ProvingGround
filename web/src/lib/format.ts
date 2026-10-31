export const pct = (v: number | null | undefined, digits = 0) =>
  v === null || v === undefined || Number.isNaN(v) ? "—" : `${(v * 100).toFixed(digits)}%`;

export const num = (v: number | null | undefined, digits = 0) =>
  v === null || v === undefined || Number.isNaN(v)
    ? "—"
    : v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });

export function usd(v: number | null | undefined, digits?: number): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const d = digits ?? (Math.abs(v) >= 100 ? 0 : Math.abs(v) >= 1 ? 2 : 4);
  return `$${v.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d })}`;
}

export function ms(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return v >= 1000 ? `${(v / 1000).toFixed(v >= 10000 ? 0 : 2)} s` : `${v.toFixed(0)} ms`;
}

export function relTime(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "—";
  const diff = (now - new Date(iso).getTime()) / 1000;
  const abs = Math.abs(diff);
  const unit = abs < 60 ? [1, "s"] : abs < 3600 ? [60, "m"] : abs < 86400 ? [3600, "h"] : [86400, "d"];
  const n = Math.floor(abs / (unit[0] as number));
  return diff >= 0 ? `${n}${unit[1]} ago` : `in ${n}${unit[1]}`;
}

export const dateTime = (iso: string | null | undefined) =>
  iso
    ? new Date(iso).toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false })
    : "—";

export const shortId = (id: string) => (id.length > 22 ? `${id.slice(4, 12)}…${id.slice(-4)}` : id);

export const modelName = (m: string | null | undefined) => (m ? m.split("/").pop()! : "—");

export const clip = (s: string | null | undefined, n = 120) => (!s ? "" : s.length > n ? `${s.slice(0, n)}…` : s);
