import { ChevronLeft, ChevronRight } from "lucide-react";
import Link from "next/link";
import { cn } from "@/lib/utils";

/** A row of link "chips" for a single-choice filter held in the URL (works without client JS). */
export function FilterChips({
  options,
  current,
  href,
  className,
}: {
  options: { value: string; label?: string }[];
  current: string;
  href: (value: string) => string;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-wrap items-center gap-1.5 text-xs", className)}>
      {options.map((o) => (
        <Link
          key={o.value}
          href={href(o.value)}
          scroll={false}
          className={cn("rounded-full border px-2.5 py-1 capitalize transition-colors hover:bg-muted", current === o.value && "border-primary bg-primary/10 font-medium text-primary")}
        >
          {o.label ?? o.value}
        </Link>
      ))}
    </div>
  );
}

export function Pager({ total, limit, offset, href }: { total: number; limit: number; offset: number; href: (offset: number) => string }) {
  if (total <= limit) return null;
  const from = offset + 1;
  const to = Math.min(total, offset + limit);
  const btn = "inline-flex size-8 items-center justify-center rounded-lg border transition-colors";
  return (
    <div className="flex items-center justify-between gap-3 text-xs text-muted-foreground">
      <span className="tabular">
        {from}–{to} of {total}
      </span>
      <span className="flex gap-1.5">
        {offset > 0 ? (
          <Link href={href(Math.max(0, offset - limit))} scroll={false} aria-label="Previous page" className={cn(btn, "hover:bg-muted")}>
            <ChevronLeft className="size-4" />
          </Link>
        ) : (
          <span className={cn(btn, "opacity-40")} aria-hidden>
            <ChevronLeft className="size-4" />
          </span>
        )}
        {to < total ? (
          <Link href={href(offset + limit)} scroll={false} aria-label="Next page" className={cn(btn, "hover:bg-muted")}>
            <ChevronRight className="size-4" />
          </Link>
        ) : (
          <span className={cn(btn, "opacity-40")} aria-hidden>
            <ChevronRight className="size-4" />
          </span>
        )}
      </span>
    </div>
  );
}

/** Build a querystring from the current params, overriding some. Empty/"all" values are dropped. */
export function withParams(base: string, current: Record<string, string | undefined>, next: Record<string, string | number | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries({ ...current, ...next })) {
    if (v === undefined || v === "" || v === "all" || v === 0 || v === "0") continue;
    q.set(k, String(v));
  }
  const s = q.toString();
  return s ? `${base}?${s}` : base;
}

export function first(v: string | string[] | undefined): string | undefined {
  return Array.isArray(v) ? v[0] : v;
}
