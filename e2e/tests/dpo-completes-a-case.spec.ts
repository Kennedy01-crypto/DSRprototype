import { expect, test } from "@playwright/test";

import { acceptDialogs, applyAction, createCase, RUN, signIn } from "./helpers";

test("a DPO takes an access request from intake to a published, closed response", async ({
  browser,
}) => {
  const patientId = `e2e-lifecycle-${RUN}`;
  const caseId = await createCase(patientId, `E2E lifecycle ${RUN}`);

  const dpo = await browser.newPage();
  acceptDialogs(dpo);
  await signIn(dpo, "dpo", `e2e-dpo-${RUN}`);
  await dpo.goto(`/dpo/requests/${caseId}`);
  await expect(dpo.getByRole("heading", { name: new RegExp(`DSR-${caseId}`) })).toBeVisible();

  await test.step("begin processing", async () => {
    await applyAction(dpo, "Begin processing");
    await expect(dpo.getByText(/Request status updated/).first()).toBeVisible();
  });

  await test.step("search CIMS automatically and wait for the worker", async () => {
    await dpo.locator("#auto-search-system").selectOption("CIMS");
    await dpo.getByRole("button", { name: "Search automatically" }).click();
    await expect(dpo.getByText("Completed by automated search.")).toBeVisible({ timeout: 90_000 });
  });

  await test.step("approve privacy review", async () => {
    // The worker may already have moved the case to Privacy Review.
    const action = dpo.locator("#workflow-action");
    if (await action.locator("option", { hasText: "Send to privacy review" }).count()) {
      await applyAction(dpo, "Send to privacy review");
      await expect(action.locator("option", { hasText: "Approve privacy review" })).toHaveCount(1);
    }
    await applyAction(dpo, "Approve privacy review");
    await expect(dpo.getByText("Ready to finalize a response.").or(dpo.getByText(/Record a final decision/))).toBeVisible();
  });

  await test.step("record the decision, finalize and publish", async () => {
    await dpo.locator("#decision-outcome").selectOption("FULFILLED");
    await dpo.locator("#decision-rationale").fill("All records were found.");
    await dpo.getByRole("button", { name: "Record decision" }).click();
    await expect(dpo.getByText("Ready to finalize a response.")).toBeVisible();

    await dpo.getByRole("button", { name: "Start from generated draft" }).click();
    await expect(dpo.locator("#response-text")).toHaveValue(/Subject: Update on your/);
    await dpo.locator("#response-text").fill(`Your records are ready. ${RUN}`);
    await dpo.getByRole("button", { name: "Finalize as version 1" }).click();
    await expect(dpo.getByText("Not visible to the patient")).toBeVisible();

    await dpo.getByRole("button", { name: "Publish to patient portal" }).click();
    await expect(dpo.getByText(/^Published by/)).toBeVisible();
  });

  await test.step("close the case", async () => {
    await applyAction(dpo, "Approve and close", { reason: "Response published.", resolution: "FULFILLED" });
    await expect(dpo.getByText("Request status updated to Completed.")).toBeVisible();
  });

  await test.step("the patient reads the response", async () => {
    const patient = await browser.newPage();
    await signIn(patient, "patient", patientId);
    await patient.goto(`/patient/requests/${caseId}`);
    await expect(patient.getByText(`Your records are ready. ${RUN}`)).toBeVisible();
    await patient.close();
  });
  await dpo.close();
});
