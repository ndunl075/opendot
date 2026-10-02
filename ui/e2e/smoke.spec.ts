import { expect, test } from "@playwright/test";

test("app shell loads", async ({ page }) => {
  await page.route("**/v1/onboarding", async route => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...await response.json(), current_step: "connections", completed_steps: ["companion", "chatgpt", "weekly_limit"] } });
  });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "OpenDot" })).toBeVisible();
});
