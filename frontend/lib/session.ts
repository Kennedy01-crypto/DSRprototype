import { createHmac, timingSafeEqual } from "node:crypto";
import { cookies } from "next/headers";

import type { DemoRole, DemoSession } from "@/lib/types";

export const SESSION_COOKIE = "dsr_demo_session";
const SESSION_MAX_AGE = 60 * 60 * 8;

function sessionSecret(): string {
  const secret = process.env.MOCK_SESSION_SECRET;
  if (secret) return secret;
  if (process.env.NODE_ENV === "development") {
    return "local-ui-demo-session-secret";
  }
  throw new Error("MOCK_SESSION_SECRET must be configured.");
}

function sign(payload: string): string {
  return createHmac("sha256", sessionSecret()).update(payload).digest("base64url");
}

function parseToken(token: string | undefined): DemoSession | null {
  if (!token) return null;
  const [payload, signature] = token.split(".");
  if (!payload || !signature) return null;

  const expected = sign(payload);
  const suppliedBuffer = Buffer.from(signature);
  const expectedBuffer = Buffer.from(expected);
  if (
    suppliedBuffer.length !== expectedBuffer.length ||
    !timingSafeEqual(suppliedBuffer, expectedBuffer)
  ) {
    return null;
  }

  try {
    const session = JSON.parse(
      Buffer.from(payload, "base64url").toString("utf8"),
    ) as Partial<DemoSession>;
    if (
      (session.role !== "patient" && session.role !== "dpo") ||
      typeof session.principalId !== "string" ||
      !/^[A-Za-z0-9_.:-]{1,128}$/.test(session.principalId)
    ) {
      return null;
    }
    return { role: session.role, principalId: session.principalId };
  } catch {
    return null;
  }
}

export async function getDemoSession(): Promise<DemoSession | null> {
  const cookieStore = await cookies();
  return parseToken(cookieStore.get(SESSION_COOKIE)?.value);
}

export function createSessionToken(session: DemoSession): string {
  const payload = Buffer.from(JSON.stringify(session)).toString("base64url");
  return `${payload}.${sign(payload)}`;
}

export function isDemoRole(value: unknown): value is DemoRole {
  return value === "patient" || value === "dpo";
}

export function sessionCookieOptions() {
  return {
    httpOnly: true,
    sameSite: "lax" as const,
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: SESSION_MAX_AGE,
  };
}
