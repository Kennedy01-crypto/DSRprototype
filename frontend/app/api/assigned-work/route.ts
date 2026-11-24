import { NextResponse } from "next/server";

import { backendError, backendRequest } from "@/lib/backend";
import { getDemoSession } from "@/lib/session";

export async function GET() {
  const session = await getDemoSession();
  if (!session) {
    return NextResponse.json({ detail: "Sign in to the demo workspace." }, { status: 401 });
  }
  if (session.role !== "department_contributor") {
    return NextResponse.json({ detail: "Department Contributor role is required." }, { status: 403 });
  }

  try {
    const upstream = await backendRequest("/api/v1/dpo/tasks/assigned/", session);
    if (!upstream.ok) {
      return NextResponse.json(
        { detail: await backendError(upstream) },
        { status: upstream.status },
      );
    }
    return NextResponse.json(await upstream.json());
  } catch {
    return NextResponse.json(
      { detail: "Unable to reach the DSR API. Check that the web service is running." },
      { status: 502 },
    );
  }
}
