import type { DemoSession } from "@/lib/types";

const backendOrigin = process.env.DSR_API_URL ?? "http://localhost:8002";

/**
 * Call the Django API as the signed-in user. Every call carries a fresh
 * X-Request-ID, which Django logs and echoes back, so a problem a user sees
 * can be found in the backend logs.
 */
export async function backendRequest(
  path: string,
  session: DemoSession,
  init: RequestInit = {},
): Promise<Response> {
  return fetch(new URL(path, backendOrigin), {
    ...init,
    cache: "no-store",
    headers: {
      Authorization: `Bearer ${session.role}:${session.principalId}`,
      "X-Request-ID": crypto.randomUUID(),
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...init.headers,
    },
  });
}

/**
 * Headers to pass through from the browser for a change to a case: the
 * revision the user's screen was showing, so the API can refuse a change
 * made from an out-of-date screen.
 */
export function caseHeaders(request: Request | undefined): Record<string, string> {
  const ifMatch = request?.headers.get("If-Match")?.trim();
  return ifMatch && /^\d{1,12}$/.test(ifMatch) ? { "If-Match": ifMatch } : {};
}

export interface BackendErrorBody {
  detail: string;
  /** Machine-readable reason, e.g. "stale_case". */
  code?: string;
}

export async function backendErrorBody(response: Response): Promise<BackendErrorBody> {
  const body = (await response.json().catch(() => null)) as Record<string, unknown> | null;
  const code = typeof body?.code === "string" ? body.code : undefined;
  let detail: string;
  if (typeof body?.detail === "string") {
    detail = body.detail;
  } else {
    const messages = body
      ? Object.values(body).flatMap((value) =>
          Array.isArray(value)
            ? value.filter((item): item is string => typeof item === "string")
            : typeof value === "string"
              ? [value]
              : [],
        )
      : [];
    detail = messages.join(" ") || `The DSR service returned ${response.status}.`;
  }
  // For server faults, give the user something to quote to support.
  const requestId = response.headers.get("X-Request-ID");
  if (response.status >= 500 && requestId) detail += ` (Reference: ${requestId.slice(0, 12)})`;
  return code ? { detail, code } : { detail };
}

export async function backendError(response: Response): Promise<string> {
  return (await backendErrorBody(response)).detail;
}
