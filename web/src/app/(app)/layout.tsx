import { MobileBar, Sidebar } from "@/components/shell/sidebar";

/** The signed-in shell. The login page sits outside this group, so it has no navigation. */
export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <MobileBar />
      <div className="flex min-h-dvh">
        <Sidebar />
        <main className="min-w-0 flex-1">{children}</main>
      </div>
    </>
  );
}
