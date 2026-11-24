import { NextResponse } from "next/server";

import { backendError, backendRequest } from "@/lib/backend";
import { getDemoSession } from "@/lib/session";

type RouteContext = {
  params: Promise<{ requestId: string; taskId: string }>;
};

export async function PATCH(request: Request, context: RouteContext) {
  const session = await getDemoSession();
  if (!session) {
    return NextResponse.json({ detail: "Sign in to the demo workspace." }, { status: 401 });
  }
  if (session.role !== "department_contributor") {
    return NextResponse.json({ detail: "Department Contributor role is required." }, { status: 403 });
  }

  const { requestId, taskId } = await context.params;
  if (!/^[1-9]\d*$/.test(requestId) || !/^[1-9]\d*$/.test(taskId)) {
    return NextResponse.json({ detail: "Invalid assigned-work reference." }, { status: 400 });
  }

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ detail: "Invalid JSON body." }, { status: 400 });
  }
  try {
    const upstream = await backendRequest(
      `/api/v1/dpo/dsrs/${requestId}/tasks/${taskId}/`,
      session,
      { method: "PATCH", body: JSON.stringify(body) },
    );
    if (!upstream.ok) {
      return NextResponse.json(
        { detail: await backendError(upstream) },
        { status: upstream.status },
      );
    }
    return NextResponse.json(await upstream.json());
  } catch {
    return NextResponse.json(
      { detail: "Unable to update assigned work. Check that the DSR API is running." },
      { status: 502 },
    );
  }
}
