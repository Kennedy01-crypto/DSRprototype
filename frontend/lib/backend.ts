import type { DemoSession } from "@/lib/types";

const backendOrigin = process.env.DSR_API_URL ?? "http://localhost:8000";

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
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...init.headers,
    },
  });
}

export async function backendError(response: Response): Promise<string> {
  const body = (await response.json().catch(() => null)) as
    | { detail?: string; non_field_errors?: string[] }
    | null;
  return (
    body?.detail ??
    body?.non_field_errors?.join(" ") ??
    `The DSR service returned ${response.status}.`
  );
}
