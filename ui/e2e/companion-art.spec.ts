import { expect, test } from "@playwright/test";
import { mkdir } from "node:fs/promises";

test("renders every original character and pet at picker and preview sizes", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.emulateMedia({ colorScheme: "light", reducedMotion: "reduce" });
  await page.goto("/e2e/fixtures/avatars.html");
  await expect(page.getByRole("img")).toHaveCount(36);
  const ids = await page.locator("svg [id]").evaluateAll(elements => elements.map(element => element.id));
  expect(new Set(ids).size).toBe(ids.length);
  expect(errors).toEqual([]);
  await mkdir(".visual-check", { recursive: true });
  for (const kind of ["characters", "pets"]) await page.locator(`[data-sheet="${kind}"]`).screenshot({ path: `.visual-check/${kind}.png` });
});
