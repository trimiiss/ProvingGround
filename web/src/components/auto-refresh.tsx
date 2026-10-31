"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

/** Re-fetches the server components on the page every few seconds while the tab is visible. */
export function AutoRefresh({ seconds = 4 }: { seconds?: number }) {
  const router = useRouter();
  useEffect(() => {
    const tick = () => {
      if (document.visibilityState === "visible") router.refresh();
    };
    const id = window.setInterval(tick, seconds * 1000);
    return () => window.clearInterval(id);
  }, [router, seconds]);
  return null;
}
