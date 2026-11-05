import { connection, NextResponse } from "next/server";
import { api } from "@/lib/api";

/** Container health check: public, so it says only whether the dashboard can reach the API. */
export async function GET(): Promise<NextResponse> {
  await connection();
  try {
    await api("/v1/config");
    return NextResponse.json({ ok: true });
  } catch {
    return NextResponse.json({ ok: false }, { status: 503 });
  }
}
