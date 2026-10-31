"use client";

import { ExternalLink, Menu } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Suspense, useState } from "react";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { cn } from "@/lib/utils";
import { NAV } from "./nav";
import { ThemeToggle } from "./theme-toggle";

const LINKS = [
  { label: "Grafana", href: process.env.NEXT_PUBLIC_GRAFANA_URL ?? "http://localhost:3001" },
  { label: "Prometheus", href: process.env.NEXT_PUBLIC_PROMETHEUS_URL ?? "http://localhost:9090" },
  { label: "API docs", href: `${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/docs` },
];

function Brand() {
  return (
    <Link href="/" className="flex items-center gap-2.5 px-2">
      <span className="grid size-8 place-items-center rounded-lg bg-primary text-primary-foreground shadow-sm">
        <svg
          viewBox="0 0 24 24"
          className="size-[18px]"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.4"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden
        >
          <path d="M4 18 9 11l4 4 7-10" />
          <circle cx="20" cy="5" r="1.2" fill="currentColor" />
        </svg>
      </span>
      <span className="text-[15px] font-semibold tracking-tight">ProvingGround</span>
    </Link>
  );
}

/** Reads the pathname for the active-link highlight. Isolated so the rest of the shell can prerender. */
function ActiveNav({ onNavigate }: { onNavigate?: () => void }) {
  return <NavList pathname={usePathname()} onNavigate={onNavigate} />;
}

function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <Suspense fallback={<NavList pathname="" onNavigate={onNavigate} />}>
      <ActiveNav onNavigate={onNavigate} />
    </Suspense>
  );
}

function NavList({ pathname, onNavigate }: { pathname: string; onNavigate?: () => void }) {
  const active = (href: string) =>
    href === "/" ? pathname === "/" || pathname.startsWith("/agents") : pathname.startsWith(href);
  return (
    <nav className="flex flex-col gap-5" aria-label="Main">
      {NAV.map((g) => (
        <div key={g.group} className="flex flex-col gap-0.5">
          <p className="px-2.5 pb-1 text-[11px] font-medium tracking-wider text-muted-foreground uppercase">{g.group}</p>
          {g.items.map(({ href, label, icon: Icon }) => (
            <Link
              key={href}
              href={href}
              onClick={onNavigate}
              aria-current={active(href) ? "page" : undefined}
              className={cn(
                "flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-sm text-sidebar-foreground/80 transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
                active(href) && "bg-sidebar-accent font-medium text-sidebar-accent-foreground",
              )}
            >
              <Icon className="size-4 shrink-0" />
              {label}
            </Link>
          ))}
        </div>
      ))}
    </nav>
  );
}

function Footer() {
  return (
    <div className="flex flex-col gap-1 border-t border-sidebar-border pt-3">
      {LINKS.map((l) => (
        <a
          key={l.label}
          href={l.href}
          target="_blank"
          rel="noreferrer"
          className="flex items-center justify-between rounded-md px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
        >
          {l.label}
          <ExternalLink className="size-3" />
        </a>
      ))}
    </div>
  );
}

export function Sidebar() {
  return (
    <aside className="sticky top-0 hidden h-dvh w-60 shrink-0 flex-col justify-between gap-6 border-r border-sidebar-border bg-sidebar px-3 py-4 md:flex">
      <div className="flex flex-col gap-6">
        <div className="flex items-center justify-between">
          <Brand />
          <ThemeToggle />
        </div>
        <NavLinks />
      </div>
      <Footer />
    </aside>
  );
}

export function MobileBar() {
  const [open, setOpen] = useState(false);
  return (
    <header className="sticky top-0 z-30 flex items-center justify-between border-b bg-background/90 px-4 py-2.5 backdrop-blur md:hidden">
      <Brand />
      <div className="flex items-center gap-1">
        <ThemeToggle />
        <Button variant="ghost" size="icon" aria-label="Open navigation" onClick={() => setOpen(true)}>
          <Menu className="size-5" />
        </Button>
      </div>
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent side="left" className="flex w-64 flex-col justify-between bg-sidebar p-4">
          <SheetTitle className="sr-only">Navigation</SheetTitle>
          <div className="flex flex-col gap-6 pt-6">
            <Brand />
            <NavLinks onNavigate={() => setOpen(false)} />
          </div>
          <Footer />
        </SheetContent>
      </Sheet>
    </header>
  );
}
