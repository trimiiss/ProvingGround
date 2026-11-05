import {
  Activity,
  Database,
  FolderUp,
  GitCompareArrows,
  LayoutDashboard,
  ListChecks,
  ListTodo,
  Scale,
  ShieldAlert,
  ShieldCheck,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
}

export const NAV: { group: string; items: NavItem[]; advanced?: boolean }[] = [
  {
    group: "Monitor",
    items: [{ href: "/", label: "Fleet", icon: LayoutDashboard }],
  },
  {
    group: "Evaluate",
    items: [
      { href: "/upload", label: "Upload agent", icon: FolderUp },
      { href: "/runs", label: "Runs", icon: ListChecks },
      { href: "/compare", label: "Compare", icon: GitCompareArrows },
      { href: "/redteam", label: "Red team", icon: ShieldAlert },
    ],
  },
  {
    group: "Advanced",
    advanced: true,
    items: [
      { href: "/gates", label: "Quality gates", icon: ShieldCheck },
      { href: "/traces", label: "Traces", icon: Activity },
      { href: "/models", label: "Models", icon: Scale },
      { href: "/datasets", label: "Datasets", icon: Database },
      { href: "/jobs", label: "Jobs", icon: ListTodo },
    ],
  },
];
