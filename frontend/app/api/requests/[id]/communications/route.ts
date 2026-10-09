import { NextResponse } from "next/server";

import { backendError, backendRequest } from "@/lib/backend";
import { getDemoSession } from "@/lib/session";

type RouteContext = { params: Promise<{ id: string }> };

async function getRequestSession(context: RouteContext) {
  const session = await getDemoSession();
  if (!session) {
    return {
      response: NextResponse.json(
        { detail: "Sign in to the demo workspace." },
        { status: 401 },
      ),
    };
  }
  const { id } = await context.params;
  if (!/^[1-9]\d*$/.test(id) || !Number.isSafeInteger(Number(id))) {
    return {
      response: NextResponse.json(
        { detail: "Invalid request reference." },
        { status: 400 },
      ),
    };
  }
  return { session, id: Number(id) };
}

export async function POST(request: Request, context: RouteContext) {
  const { response, session, id } = await getRequestSession(context);
  if (response) return response;

  let body: { message?: unknown };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ detail: "Invalid JSON body." }, { status: 400 });
  }
  if (typeof body.message !== "string" || !body.message.trim()) {
    return NextResponse.json(
      { detail: "Enter a message before sending it." },
      { status: 400 },
    );
  }

  try {
    const endpoint =
      session.role === "dpo"
        ? `/api/v1/dpo/dsrs/${id}/communications/`
        : `/api/v1/dsrs/${id}/communications/`;
    const upstream = await backendRequest(
      endpoint,
      session,
      { method: "POST", body: JSON.stringify({ message: body.message.trim() }) },
    );
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
