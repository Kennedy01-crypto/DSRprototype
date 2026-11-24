import { NextResponse } from "next/server";

import { backendErrorBody, backendRequest, caseHeaders } from "@/lib/backend";
import { getDemoSession } from "@/lib/session";

const RECORD_ID = /^[1-9]\d*$/;

export function invalidIdResponse() {
  return NextResponse.json({ detail: "Invalid request reference." }, { status: 400 });
}

export function isRecordId(...ids: string[]): boolean {
  return ids.every((id) => RECORD_ID.test(id));
}

/**
 * Forward a DPO-only request to the Django API with the signed-in identity.
 * The backend enforces the real rules; this only rejects obvious misuse early
 * and turns backend errors into `{ detail }` for the UI.
 */
export async function proxyDpo(
  path: string,
  method: "GET" | "POST" | "PATCH",
  request?: Request,
): Promise<NextResponse> {
  const session = await getDemoSession();
  if (!session) {
    return NextResponse.json({ detail: "Sign in to the demo workspace." }, { status: 401 });
  }
  if (session.role !== "dpo") {
    return NextResponse.json({ detail: "Only the DPO workspace can do this." }, { status: 403 });
  }
  let body: string | undefined;
  if (method !== "GET") {
    try {
      body = JSON.stringify(request ? await request.json() : {});
    } catch {
      body = JSON.stringify({});
    }
  }
  try {
    const upstream = await backendRequest(path, session, {
      method,
      headers: caseHeaders(request),
      ...(body === undefined ? {} : { body }),
    });
    if (!upstream.ok) {
      return NextResponse.json(await backendErrorBody(upstream), { status: upstream.status });
    }
    return NextResponse.json(await upstream.json(), { status: upstream.status });
  } catch {
    return NextResponse.json(
      { detail: "Unable to reach the DSR API. Check that the web service is running." },
      { status: 502 },
    );
  }
}
