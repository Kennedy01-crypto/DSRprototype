import { request } from "@playwright/test";

/**
 * The dev frontend compiles each page and API route on first use, which can
 * take over a minute on a slow file system. Visit them once up front so
 * test timeouts measure the app, not the compiler.
 */
export default async function warmUp(): Promise<void> {
  const baseURL = process.env.E2E_BASE_URL ?? "http://localhost:3002";
  const context = await request.newContext({ baseURL, timeout: 300_000 });
  await context.post("/api/session", { data: { role: "dpo", principalId: "e2e-warmup" } });
  for (const path of [
    "/login",
    "/patient",
    "/dpo/requests",
    "/dpo/requests/1",
    "/dpo/integrations",
    "/api/requests",
    "/api/requests/1",
    "/api/integration-jobs",
  ]) {
    await context.get(path, { failOnStatusCode: false });
  }
  await context.dispose();
}
