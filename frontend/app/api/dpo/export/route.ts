import { NextResponse } from "next/server";

import { backendError, backendRequest } from "@/lib/backend";
import { getDemoSession } from "@/lib/session";

const FILTERS = ["state", "type", "department", "assigned_to", "queue"] as const;

/** Download the DPO queue as CSV, using the same filters as the queue view. */
export async function GET(request: Request) {
  const session = await getDemoSession();
  if (!session) {
    return NextResponse.json({ detail: "Sign in to the demo workspace." }, { status: 401 });
  }
  if (session.role !== "dpo") {
    return NextResponse.json({ detail: "Only the DPO workspace can export cases." }, { status: 403 });
  }
  const incoming = new URL(request.url).searchParams;
  const params = new URLSearchParams();
  for (const name of FILTERS) {
    const value = incoming.get(name)?.trim();
    if (value) params.set(name, value);
  }
  const query = params.size ? `?${params.toString()}` : "";
  try {
    const upstream = await backendRequest(`/api/v1/dpo/dsrs/export/${query}`, session);
    if (!upstream.ok) {
      return NextResponse.json({ detail: await backendError(upstream) }, { status: upstream.status });
    }
    return new NextResponse(await upstream.text(), {
      status: 200,
      headers: {
        "Content-Type": "text/csv; charset=utf-8",
        "Content-Disposition": 'attachment; filename="dsr-case-report.csv"',
        "Cache-Control": "no-store",
      },
    });
  } catch {
    return NextResponse.json(
      { detail: "Unable to reach the DSR API. Check that the web service is running." },
      { status: 502 },
    );
  }
}
