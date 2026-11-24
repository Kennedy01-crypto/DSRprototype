import { expect, request, type Page } from "@playwright/test";

const API_URL = process.env.E2E_API_URL ?? "http://localhost:8002";

/** A per-run suffix so tests never collide with existing data. */
export const RUN = Date.now().toString(36);

type Role = "patient" | "dpo" | "department_contributor" | "independent_reviewer";

/** Sign in through the demo session API; the cookie is shared with the page. */
export async function signIn(page: Page, role: Role, principalId: string): Promise<void> {
  const response = await page.request.post("/api/session", {
    data: { role, principalId },
    timeout: 120_000,
  });
  expect(response.ok()).toBeTruthy();
}

/** Accept the confirmation dialogs the workspace shows before changes. */
export function acceptDialogs(page: Page): void {
  page.on("dialog", (dialog) => void dialog.accept());
}

/** Create a request directly through the API, as the given patient. */
export async function createCase(patientId: string, description: string): Promise<number> {
  const api = await request.newContext({
    baseURL: API_URL,
    timeout: 120_000,
    extraHTTPHeaders: { Authorization: `Bearer patient:${patientId}` },
  });
  const response = await api.post("/api/v1/dsrs/submit/", {
    data: { request_type: "ACCESS", description, department: "Records" },
  });
  expect(response.status()).toBe(201);
  const id = (await response.json()).id as number;
  await api.dispose();
  return id;
}

/** Pick a workflow action on the DPO case page and apply it. */
export async function applyAction(
  page: Page,
  label: string,
  options: { reason?: string; resolution?: string } = {},
): Promise<void> {
  await page.locator("#workflow-action").selectOption({ label });
  if (options.resolution) await page.locator("#resolution-status").selectOption(options.resolution);
  await page.locator("#workflow-reason").fill(options.reason ?? "");
  await page.getByRole("button", { name: "Apply action" }).click();
}
