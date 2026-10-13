import { NextResponse } from "next/server";

import { backendError, backendRequest } from "@/lib/backend";
import { getDemoSession } from "@/lib/session";

export async function GET() {
  const session = await getDemoSession();
  if (!session) {
    return NextResponse.json(
      { detail: "Sign in to the DPO workspace." },
      { status: 401 },
    );
  }
  if (session.role !== "dpo") {
    return NextResponse.json(
      { detail: "Only the DPO role can manage deadline policies." },
      { status: 403 },
    );
  }

  try {
    const upstream = await backendRequest("/api/v1/dpo/policies/", session);
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

export async function PATCH(request: Request) {
  const session = await getDemoSession();
  if (!session) {
    return NextResponse.json(
      { detail: "Sign in to the DPO workspace." },
      { status: 401 },
    );
  }
  if (session.role !== "dpo") {
    return NextResponse.json(
      { detail: "Only the DPO role can manage deadline policies." },
      { status: 403 },
    );
  }

  try {
    const body = await request.json();
    const upstream = await backendRequest("/api/v1/dpo/policies/", session, {
      method: "PATCH",
      body: JSON.stringify(body),
    });
    if (!upstream.ok) {
      return NextResponse.json(
        { detail: await backendError(upstream) },
        { status: upstream.status },
      );
    }
    return NextResponse.json(await upstream.json());
  } catch {
    return NextResponse.json(
      { detail: "Unable to save the deadline policy. Check that the DSR API is running." },
      { status: 502 },
    );
  }
}
