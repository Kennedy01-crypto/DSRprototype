import { NextResponse } from "next/server";

import { backendError, backendRequest } from "@/lib/backend";
import { getDemoSession } from "@/lib/session";

type RouteContext = { params: Promise<{ id: string }> };

async function getRequestId(context: RouteContext): Promise<number | null> {
  const { id } = await context.params;
  if (!/^[1-9]\d*$/.test(id)) return null;
  const requestId = Number(id);
  return Number.isSafeInteger(requestId) ? requestId : null;
}

async function getSession() {
  const session = await getDemoSession();
  if (!session) {
    return {
      response: NextResponse.json(
        { detail: "Sign in to the demo workspace." },
        { status: 401 },
      ),
    };
  }
  return { session };
}

async function proxyRequest(
  path: string,
  session: NonNullable<Awaited<ReturnType<typeof getDemoSession>>>,
  method: "GET" | "PATCH" | "POST",
  body?: unknown,
) {
  try {
    const upstream = await backendRequest(path, session, {
      method,
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    if (!upstream.ok) {
      return NextResponse.json(
        { detail: await backendError(upstream) },
        { status: upstream.status },
      );
    }
    return NextResponse.json(await upstream.json(), { status: upstream.status });
  } catch {
    return NextResponse.json(
      { detail: "Unable to reach the DSR API. Check that the web service is running." },
      { status: 502 },
    );
  }
}

export async function GET(_request: Request, context: RouteContext) {
  const { response, session } = await getSession();
  if (response) return response;
  const id = await getRequestId(context);
  if (id === null) {
    return NextResponse.json({ detail: "Invalid request reference." }, { status: 400 });
  }
  const path =
    session.role === "dpo"
      ? `/api/v1/dpo/dsrs/${id}/`
      : `/api/v1/dsrs/${id}/`;
  return proxyRequest(path, session, "GET");
}

export async function PATCH(request: Request, context: RouteContext) {
  const { response, session } = await getSession();
  if (response) return response;
  if (session.role !== "dpo") {
    return NextResponse.json(
      { detail: "Only the DPO workspace can assign requests." },
      { status: 403 },
    );
  }
  const id = await getRequestId(context);
  if (id === null) {
    return NextResponse.json({ detail: "Invalid request reference." }, { status: 400 });
  }
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ detail: "Invalid JSON body." }, { status: 400 });
  }
  return proxyRequest(`/api/v1/dpo/dsrs/${id}/assignment/`, session, "POST", body);
}

export async function POST(request: Request, context: RouteContext) {
  const { response, session } = await getSession();
  if (response) return response;
  const id = await getRequestId(context);
  if (id === null) {
    return NextResponse.json({ detail: "Invalid request reference." }, { status: 400 });
  }
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ detail: "Invalid JSON body." }, { status: 400 });
  }
  const path =
    session.role === "dpo"
      ? `/api/v1/dpo/dsrs/${id}/transition/`
      : `/api/v1/dsrs/${id}/withdraw/`;
  return proxyRequest(path, session, "POST", body);
}
