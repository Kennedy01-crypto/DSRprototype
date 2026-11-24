import { expect, test } from "@playwright/test";

import { RUN, signIn } from "./helpers";

test("a patient submits a request once, even when they click twice", async ({ page }) => {
  await signIn(page, "patient", `e2e-patient-${RUN}`);
  await page.goto("/patient");

  const description = `E2E copy of my records ${RUN}`;
  await page.locator("#request-type").selectOption("ACCESS");
  await page.locator("#request-description").fill(description);
  const submit = page.getByRole("button", { name: "Submit mock request" });
  await submit.dblclick();

  await expect(page.getByText("Your mock request was submitted.").first()).toBeVisible();
  await expect(page.getByText(description)).toHaveCount(1);
});
