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

export const NAV: { group: string; items: NavItem[] }[] = [
  {
    group: "Monitor",
    items: [
      { href: "/", label: "Fleet", icon: LayoutDashboard },
      { href: "/traces", label: "Traces", icon: Activity },
    ],
  },
  {
    group: "Evaluate",
    items: [
      { href: "/upload", label: "Upload agent", icon: FolderUp },
      { href: "/runs", label: "Runs", icon: ListChecks },
      { href: "/compare", label: "Compare", icon: GitCompareArrows },
      { href: "/redteam", label: "Red team", icon: ShieldAlert },
      { href: "/models", label: "Models", icon: Scale },
    ],
  },
  {
    group: "Release",
    items: [
      { href: "/gates", label: "Quality gates", icon: ShieldCheck },
      { href: "/datasets", label: "Datasets", icon: Database },
      { href: "/jobs", label: "Jobs", icon: ListTodo },
    ],
  },
];
