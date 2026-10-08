import { NextResponse } from "next/server";

import { backendError, backendRequest } from "@/lib/backend";
import { getDemoSession } from "@/lib/session";

const FILTERS = ["state", "type", "assigned_to", "queue"] as const;

export async function GET(request: Request) {
  const session = await getDemoSession();
  if (!session) {
    return NextResponse.json({ detail: "Sign in to the demo workspace." }, { status: 401 });
  }

  const path =
    session.role === "patient"
      ? "/api/v1/dsrs/my-requests/"
      : `/api/v1/dpo/dsrs/${buildQuery(new URL(request.url).searchParams)}`;

  try {
    const upstream = await backendRequest(path, session);
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

export async function POST(request: Request) {
  const session = await getDemoSession();
  if (!session) {
    return NextResponse.json({ detail: "Sign in to the demo workspace." }, { status: 401 });
  }
  if (session.role !== "patient") {
    return NextResponse.json(
      { detail: "Only the patient workspace can submit a request." },
      { status: 403 },
    );
  }

  try {
    const body = await request.json();
    const upstream = await backendRequest("/api/v1/dsrs/submit/", session, {
      method: "POST",
      body: JSON.stringify(body),
    });
    if (!upstream.ok) {
      const errorBody = await upstream.json().catch(() => null);
      return NextResponse.json(
        errorBody ?? { detail: await backendError(upstream) },
        { status: upstream.status },
      );
    }
    return NextResponse.json(await upstream.json(), { status: upstream.status });
  } catch {
    return NextResponse.json(
      { detail: "Unable to submit the request. Check that the DSR API is running." },
      { status: 502 },
    );
  }
}

function buildQuery(params: URLSearchParams): string {
  const filtered = new URLSearchParams();
  for (const key of FILTERS) {
    const value = params.get(key);
    if (value) filtered.set(key, value);
  }
  const query = filtered.toString();
  return query ? `?${query}` : "";
}
