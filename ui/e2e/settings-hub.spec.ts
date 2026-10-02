import { expect, test } from "@playwright/test";
import { mkdir } from "node:fs/promises";

const sections = ["General", "Companion", "Connections", "Memory", "Rules", "Usage", "Activity", "About"];

test.beforeEach(async ({ page }) => {
  await page.route("**/v1/onboarding", async route => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...await response.json(), current_step: "done", completed_steps: ["done"] } });
  });
});

// About can render before the setup request finishes. Let its route handler
// settle before Playwright disposes the response context at test teardown.
test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: "wait" });
});

test("desktop settings has only the requested rail, ordered sections and keyboard focus", async ({ page }) => {
  await page.goto("/settings");
  const rail = page.locator(".sidebar");
  await expect(rail.getByRole("navigation").getByRole("link")).toHaveText(["Chat", "Settings"]);
  await expect(rail.getByRole("link")).toHaveCount(4); // Mark, Chat, Settings, initials.
  await expect(rail.getByRole("button", { name: "Sign out" })).toBeVisible();
  await expect(rail.getByRole("link", { name: "OpenDot home" })).toBeVisible();
  await expect(rail.getByRole("link", { name: "Local workspace settings" })).toHaveText("OD");
  const navigation = page.getByRole("navigation", { name: "Settings sections" });
  await expect(navigation.getByRole("listitem")).toHaveCount(8);
  await expect(navigation.getByRole("link")).toHaveText(sections);
  await expect(page.getByRole("heading", { name: "General", level: 1 })).toBeFocused();
  await expect(navigation.locator('[aria-current="page"]')).toHaveText("General");
  for (const name of sections) {
    const link = navigation.getByRole("link", { name, exact: true });
    await link.focus();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(`/settings/${name.toLowerCase()}`);
    await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
    await expect(page.getByRole("heading", { name, level: 1 })).toBeFocused();
    await expect(navigation.locator('[aria-current="page"]')).toHaveText(name);
    await expect(rail.getByRole("link", { name: "Settings", exact: true })).toHaveAttribute("aria-current", "page");
    await expect(page.locator(".breadcrumb, .settings-pane .page-heading")).toHaveCount(0);
  }
  await navigation.getByRole("link", { name: "General" }).click();
  await expect(page.getByText("Run setup again", { exact: true })).toHaveCount(0);
  await page.getByRole("combobox", { name: "Appearance" }).click();
  await page.getByRole("option", { name: "Dark", exact: true }).click();
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(page.getByRole("combobox", { name: "Appearance" })).toContainText("Dark");
});

for (const name of sections.slice(1)) {
  test(`legacy ${name} redirects preserve query, hash and browser history`, async ({ page }) => {
    await page.goto("/chat");
    await expect(page.getByRole("textbox", { name: "Message your companion" })).toBeVisible();
    const section = name.toLowerCase();
    await page.goto(`/${section}?source=notification#item%2Ftarget`);
    await expect(page).toHaveURL(`/settings/${section}?source=notification#item%2Ftarget`);
    await expect(page.getByRole("heading", { name, level: 1 })).toBeVisible();
    await page.reload();
    await expect(page.getByRole("heading", { name, level: 1 })).toBeFocused();
    await page.goBack();
    await expect(page).toHaveURL(/\/chat$/);
    await expect(page.getByRole("textbox", { name: "Message your companion" })).toBeVisible();
  });
}

test("completed setup cannot be reopened from its old URL", async ({ page }) => {
  await page.goto("/onboarding");
  await expect(page).toHaveURL(/\/chat$/);
  await expect(page.getByRole("textbox", { name: "Message your companion" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Onboarding", exact: true })).toHaveCount(0);
});

for (const theme of ["light", "dark"] as const) {
  test(`${theme} settings panes fit desktop and mobile with a keyboard back action`, async ({ page }) => {
    await mkdir(".visual-check", { recursive: true });
    await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
    for (const width of [1366, 900, 390, 320]) {
      await page.setViewportSize({ width, height: 844 });
      await page.goto("/settings");
      const mobile = width <= 800;
      const directory = page.getByRole("navigation", { name: "Settings sections" });
      if (mobile) {
        await expect(page.getByRole("heading", { name: "Settings", level: 1 })).toBeFocused();
        await expect(page.getByRole("region", { name: "General", exact: true })).toHaveCount(0);
        await page.screenshot({ path: `.visual-check/settings-list-${width}-${theme}.png` });
      }
      for (const name of sections) {
        await directory.getByRole("link", { name, exact: true }).click();
        await expect(page.getByRole("heading", { name, level: 1 })).toBeFocused();
        await expect(page.locator("main .skeleton")).toHaveCount(0);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        await expect(directory).toBeVisible({ visible: !mobile });
        await page.screenshot({ path: `.visual-check/settings-${name.toLowerCase()}-${width}-${theme}.png`, fullPage: true });
        if (mobile) {
          const back = page.getByRole("link", { name: "Back to Settings" });
          await back.focus();
          await page.keyboard.press("Enter");
          await expect(page).toHaveURL(/\/settings$/);
          await expect(page.getByRole("heading", { name: "Settings", level: 1 })).toBeFocused();
          await expect(directory).toBeVisible();
        }
      }
    }
  });
}
