import { NextResponse } from "next/server";

import {
  createSessionToken,
  getDemoSession,
  isDemoRole,
  SESSION_COOKIE,
  sessionCookieOptions,
} from "@/lib/session";

export async function GET() {
  return NextResponse.json({ session: await getDemoSession() });
}

export async function POST(request: Request) {
  let body: { role?: unknown; principalId?: unknown };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ detail: "Invalid JSON body." }, { status: 400 });
  }

  if (
    !isDemoRole(body.role) ||
    typeof body.principalId !== "string" ||
    !/^[A-Za-z0-9_.:-]{1,128}$/.test(body.principalId)
  ) {
    return NextResponse.json(
      { detail: "Choose a demo role and enter a valid mock ID." },
      { status: 400 },
    );
  }

  const response = NextResponse.json({ role: body.role });
  response.cookies.set(
    SESSION_COOKIE,
    createSessionToken({ role: body.role, principalId: body.principalId }),
    sessionCookieOptions(),
  );
  return response;
}

export async function DELETE() {
  const response = NextResponse.json({ signedOut: true });
  response.cookies.set(SESSION_COOKIE, "", { ...sessionCookieOptions(), maxAge: 0 });
  return response;
}
