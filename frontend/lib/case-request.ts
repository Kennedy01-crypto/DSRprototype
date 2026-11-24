/**
 * Browser helper for changes to a case. Sends the revision the screen is
 * showing (If-Match), so the API refuses a change made from an out-of-date
 * screen instead of silently overwriting someone else's newer change.
 */

export class StaleCaseError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "StaleCaseError";
  }
}

export async function caseRequest<T = unknown>(
  url: string,
  {
    method,
    payload,
    revision,
    fallbackError = "The change could not be saved.",
  }: {
    method: "POST" | "PATCH";
    payload?: object;
    /** The case revision shown on screen; omit to skip the check. */
    revision?: number;
    fallbackError?: string;
  },
): Promise<T> {
  const response = await fetch(url, {
    method,
    headers: {
      "Content-Type": "application/json",
      ...(revision !== undefined ? { "If-Match": String(revision) } : {}),
    },
    body: JSON.stringify(payload ?? {}),
  });
  const body = (await response.json().catch(() => ({}))) as { detail?: string; code?: string };
  if (!response.ok) {
    if (response.status === 409 && body.code === "stale_case") {
      throw new StaleCaseError(body.detail ?? "This case changed. It has been reloaded.");
    }
    throw new Error(body.detail ?? fallbackError);
  }
  return body as T;
}
