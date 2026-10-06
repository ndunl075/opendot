import { expect, test } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import type { Settings, SettingsUpdateRequest } from "../src/api/types.gen";

const sections = ["General", "Availability", "Models", "Providers", "Backup", "Companion", "Connections", "Memory", "Rules", "Usage", "Activity", "About"];

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

test("Appearance lives only in General and preserves explicit and system themes", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "light", reducedMotion: "reduce" });
  await page.goto("/chat");
  await expect(page.getByText("Connected", { exact: true })).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Appearance" })).toHaveCount(0);
  await expect(page.locator(".chat-plan")).toHaveCount(0);
  const empty = page.locator(".transcript .empty-state");
  await expect(empty.getByRole("heading", { name: "What’s on your mind?" })).toBeVisible();
  await expect(empty.locator(".state-icon svg")).toBeVisible();
  await expect(empty.locator("p")).toHaveCount(0);
  await mkdir(".visual-check", { recursive: true });
  await page.screenshot({ path: ".visual-check/tidy-chat-empty-light.png" });

  await page.getByRole("navigation", { name: "Main navigation" }).getByRole("link", { name: "Settings" }).click();
  const appearance = page.getByRole("region", { name: "General", exact: true }).getByRole("combobox", { name: "Appearance" });
  await expect(appearance).toHaveText("System");
  await expect(page.locator(".sidebar").getByRole("combobox", { name: "Appearance" })).toHaveCount(0);
  for (const theme of ["Dark", "Light"] as const) {
    await appearance.click();
    await page.getByRole("option", { name: theme, exact: true }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme.toLowerCase());
    expect(await page.evaluate(() => localStorage.getItem("opendot-theme"))).toBe(theme.toLowerCase());
    await page.reload();
    await expect(appearance).toHaveText(theme);
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme.toLowerCase());
    await appearance.click();
    await page.screenshot({ path: `.visual-check/tidy-settings-appearance-${theme.toLowerCase()}.png` });
    await appearance.press("Escape");
  }
  await appearance.click();
  await page.getByRole("option", { name: "System", exact: true }).click();
  expect(await page.evaluate(() => localStorage.getItem("opendot-theme"))).toBeNull();
  await page.emulateMedia({ colorScheme: "dark" });
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.getByRole("navigation", { name: "Main navigation" }).getByRole("link", { name: "Chat" }).click();
  await expect(page.getByRole("combobox", { name: "Appearance" })).toHaveCount(0);
  await expect(empty).toBeVisible();
  await page.screenshot({ path: ".visual-check/tidy-chat-empty-dark.png" });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator(".chat-composer")).toBeInViewport({ ratio: 1 });
  await expect(empty).toBeInViewport({ ratio: 1 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: ".visual-check/tidy-chat-empty-mobile.png" });
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
  await expect(navigation.getByRole("listitem")).toHaveCount(12);
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

test("Settings retains plan attribution and usage management", async ({ page }) => {
  await page.goto("/settings/models");
  const models = page.getByRole("region", { name: "Plan models" });
  await expect(models.getByText("Using ChatGPT plan", { exact: true })).toBeVisible();
  await expect(models.getByRole("heading", { name: "Model selected automatically" })).toBeVisible();
  await expect(models.getByRole("link", { name: "Manage usage" })).toHaveAttribute("href", /^https:\/\/chatgpt\.com\//);
  await page.screenshot({ path: ".visual-check/tidy-settings-models.png" });
  await page.getByRole("navigation", { name: "Settings sections" }).getByRole("link", { name: "Providers", exact: true }).click();
  await expect(page.getByText("Using ChatGPT plan", { exact: true })).toBeVisible();
  await page.getByRole("navigation", { name: "Settings sections" }).getByRole("link", { name: "Usage", exact: true }).click();
  await expect(page.getByText("Using ChatGPT plan", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Manage usage" })).toHaveAttribute("href", /^https:\/\/chatgpt\.com\//);
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

test("focused settings routes save only their fields and preserve other sections", async ({ page }) => {
  let settings: Settings = {
    style_preset: "concise", auto_top_tier: false, tier_overrides: [], providers: [],
    keep_awake: { enabled: false, only_while_plugged_in: true },
    quiet_hours: { enabled: false, start: "22:00", end: "07:00", timezone: "UTC" },
  };
  const updates: SettingsUpdateRequest[] = [];
  await page.route("**/v1/settings", async route => {
    if (route.request().method() === "PUT") {
      const body = route.request().postDataJSON() as SettingsUpdateRequest;
      updates.push(body);
      settings = { ...settings, ...body } as Settings;
    }
    await route.fulfill({ json: settings });
  });
  await page.goto("/settings/general");
  await page.getByRole("combobox", { name: "Style preset" }).click();
  await page.getByRole("option", { name: "Warm", exact: true }).click();
  // Simulate another client's change while this General form is open.
  settings.keep_awake = { enabled: true, only_while_plugged_in: false };
  await page.getByRole("button", { name: "Save general" }).click();
  await expect(page.getByRole("status")).toHaveText("Saved");
  expect(updates.at(-1)).toEqual({ style_preset: "warm" });
  expect(settings.keep_awake).toEqual({ enabled: true, only_while_plugged_in: false });
  await expect(page.getByRole("switch", { name: "Keep awake" })).toHaveCount(0);

  await page.goto("/settings/availability");
  await expect(page.getByRole("heading", { name: "Availability", level: 1 })).toBeFocused();
  await expect(page.getByRole("switch", { name: "Keep awake" })).toBeChecked();
  await page.getByLabel("Quiet hours timezone").fill("America/New_York");
  settings.style_preset = "formal";
  await page.getByRole("button", { name: "Save availability" }).click();
  await expect(page.getByRole("status")).toHaveText("Saved");
  expect(updates.at(-1)).toEqual({
    keep_awake: { enabled: true, only_while_plugged_in: false },
    quiet_hours: { enabled: false, start: "22:00", end: "07:00", timezone: "America/New_York" },
  });
  expect(settings.style_preset).toBe("formal");

  await page.goto("/settings/models");
  await expect(page.getByRole("heading", { name: "Models", level: 1 })).toBeFocused();
  await page.getByRole("button", { name: "Add model override" }).click();
  await page.getByLabel("Job type 1").fill("review");
  await page.getByRole("button", { name: "Save models" }).click();
  await expect(page.getByRole("status")).toHaveText("Saved");
  expect(updates.at(-1)).toEqual({ tier_overrides: [{ job_type: "review", tier: "luna", effort: "low" }] });
  await page.getByRole("switch", { name: "Automatic top-tier use" }).click();
  const count = updates.length;
  await page.getByRole("dialog").getByRole("button", { name: "Cancel" }).click();
  expect(updates).toHaveLength(count);
  await page.getByRole("switch", { name: "Automatic top-tier use" }).click();
  await page.getByRole("button", { name: "Allow top-tier use" }).click();
  await expect(page.getByRole("switch", { name: "Automatic top-tier use" })).toBeChecked();
  expect(updates.at(-1)).toEqual({ auto_top_tier: true });
  await page.reload();
  await expect(page.getByLabel("Job type 1")).toHaveValue("review");
  await page.goto("/settings/general");
  await expect(page.getByRole("combobox", { name: "Style preset" })).toContainText("Formal");
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
