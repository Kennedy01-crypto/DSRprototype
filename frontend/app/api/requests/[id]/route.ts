import { NextResponse } from "next/server";

import { backendError, backendErrorBody, backendRequest, caseHeaders } from "@/lib/backend";
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
  if (session.role === "admin") {
    return {
      response: NextResponse.json(
        { detail: "The admin workspace cannot access request records." },
        { status: 403 },
      ),
    };
  }
  if (session.role === "department_contributor") {
    return {
      response: NextResponse.json(
        { detail: "Use the assigned-work workspace to access department tasks." },
        { status: 403 },
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
  request?: Request,
) {
  try {
    const upstream = await backendRequest(path, session, {
      method,
      headers: caseHeaders(request),
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
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

export async function GET(_request: Request, context: RouteContext) {
  const { response, session } = await getSession();
  if (response) return response;
  const id = await getRequestId(context);
  if (id === null) {
    return NextResponse.json({ detail: "Invalid request reference." }, { status: 400 });
  }
  if (session.role === "patient") {
    const path = `/api/v1/dsrs/${id}/`;
    try {
      const [requestResponse, communicationResponse, publishedResponse] =
        await Promise.all([
          backendRequest(path, session),
          backendRequest(`/api/v1/dsrs/${id}/communications/`, session),
          backendRequest(`/api/v1/dsrs/${id}/responses/`, session),
        ]);
      for (const upstream of [
        requestResponse,
        communicationResponse,
        publishedResponse,
      ]) {
        if (!upstream.ok) {
          return NextResponse.json(
            { detail: await backendError(upstream) },
            { status: upstream.status },
          );
        }
      }
      const [requestData, communications, publishedResponses] =
        await Promise.all([
          requestResponse.json(),
          communicationResponse.json(),
          publishedResponse.json(),
        ]);
      return NextResponse.json({
        ...requestData,
        communications,
        published_responses: publishedResponses,
      });
    } catch {
      return NextResponse.json(
        { detail: "Unable to reach the DSR API. Check that the web service is running." },
        { status: 502 },
      );
    }
  }
  const path = `/api/v1/dpo/dsrs/${id}/`;
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
  return proxyRequest(`/api/v1/dpo/dsrs/${id}/assignment/`, session, "POST", body, request);
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
  if (
    session.role !== "dpo" &&
    session.role !== "independent_reviewer" &&
    session.role !== "patient"
  ) {
    return NextResponse.json(
      { detail: "This workspace cannot apply case workflow actions." },
      { status: 403 },
    );
  }
  const path =
    session.role === "dpo" || session.role === "independent_reviewer"
      ? `/api/v1/dpo/dsrs/${id}/transition/`
      : `/api/v1/dsrs/${id}/withdraw/`;
  return proxyRequest(path, session, "POST", body, request);
}
