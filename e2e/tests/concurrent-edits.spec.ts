import { expect, test } from "@playwright/test";

import { acceptDialogs, createCase, RUN, signIn } from "./helpers";

test("a second DPO working from an out-of-date screen is told and shown the latest case", async ({
  browser,
}) => {
  const caseId = await createCase(`e2e-concurrent-${RUN}`, `E2E concurrent ${RUN}`);

  const contextA = await browser.newContext();
  const contextB = await browser.newContext();
  const first = await contextA.newPage();
  const second = await contextB.newPage();
  for (const [page, id] of [
    [first, "officer-a"],
    [second, "officer-b"],
  ] as const) {
    acceptDialogs(page);
    await signIn(page, "dpo", `e2e-${id}-${RUN}`);
    await page.goto(`/dpo/requests/${caseId}`);
    await expect(page.locator("#assigned-to")).toBeVisible();
  }

  await first.locator("#assigned-to").fill("officer-a");
  await first.getByRole("button", { name: "Save assignment" }).click();
  await expect(first.getByText("Request assignment saved.")).toBeVisible();

  // The second screen still shows the case as it was before that change.
  await second.locator("#assigned-to").fill("officer-b");
  await second.getByRole("button", { name: "Save assignment" }).click();
  await expect(second.getByText(/changed by someone else/)).toBeVisible();
  // It reloaded, so it now shows the first officer's assignment.
  await expect(second.locator("#assigned-to")).toHaveValue("officer-a");

  await contextA.close();
  await contextB.close();
});
