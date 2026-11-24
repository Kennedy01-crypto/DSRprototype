import { NextResponse } from "next/server";

import { backendError, backendRequest } from "@/lib/backend";
import { getDemoSession } from "@/lib/session";

type RouteContext = { params: Promise<{ id: string }> };

export async function PATCH(request: Request, context: RouteContext) {
  const session = await getDemoSession();
  if (!session) {
    return NextResponse.json({ detail: "Sign in to the demo workspace." }, { status: 401 });
  }
  if (session.role !== "dpo") {
    return NextResponse.json({ detail: "Only DPOs can classify intake jurisdiction." }, { status: 403 });
  }
  const { id } = await context.params;
  if (!/^[1-9]\d*$/.test(id)) {
    return NextResponse.json({ detail: "Invalid request reference." }, { status: 400 });
  }
  let body: string;
  try {
    body = JSON.stringify(await request.json());
  } catch {
    return NextResponse.json({ detail: "Invalid JSON body." }, { status: 400 });
  }
  try {
    const upstream = await backendRequest(
      `/api/v1/dpo/dsrs/${id}/jurisdiction/`,
      session,
      { method: "PATCH", body },
    );
    if (!upstream.ok) {
      return NextResponse.json({ detail: await backendError(upstream) }, { status: upstream.status });
    }
    return NextResponse.json(await upstream.json(), { status: upstream.status });
  } catch {
    return NextResponse.json(
      { detail: "Unable to classify jurisdiction. Check that the DSR API is running." },
      { status: 502 },
    );
  }
}
