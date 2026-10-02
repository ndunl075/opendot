import { expect, test } from "@playwright/test";
import { mkdir } from "node:fs/promises";

test.beforeEach(async ({ page }) => {
  await page.route("**/v1/onboarding", async route => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...await response.json(), current_step: "done", completed_steps: ["done"] } });
  });
});

test("chat rail, bubbles, composer, history and details remain usable", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/chat");
  await expect(page.getByText("Connected", { exact: true })).toBeVisible();
  expect((await page.locator(".sidebar").boundingBox())?.width).toBe(84);
  for (const label of ["Chat", "Activity", "Memory", "Rules", "Connections", "Usage", "Settings", "Companion"]) {
    const link = page.getByRole("navigation", { name: "Main navigation" }).getByRole("link", { name: label, exact: true });
    await expect(link.locator("span")).toBeVisible();
    expect(await link.locator("span").evaluate(element => element.getBoundingClientRect().height)).toBeGreaterThan(11);
    expect(await link.locator("span").evaluate(element => getComputedStyle(element).fontSize)).toBe("11px");
  }
  const send = page.getByRole("button", { name: "Send message", exact: true });
  await expect(send).toBeDisabled();
  await page.getByLabel("Message your companion").fill("Draft a reply");
  await expect(send).toBeEnabled();
  await send.click();
  await expect(page.getByLabel("Your message")).toContainText("Draft a reply");
  await expect(page.getByText("mock-model-terra", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Approve", exact: true })).toBeVisible();
  const bubbleColors = await page.locator(".chat-message--user").evaluate(element => ({ fg: getComputedStyle(element).color, bg: getComputedStyle(element).backgroundColor }));
  expect(bubbleColors).toEqual({ fg: "rgb(255, 255, 255)", bg: "rgb(38, 120, 204)" });
  await expect(page.getByLabel("Message read time")).toContainText("Read ");
  await expect(page.getByPlaceholder("Send a message")).toBeVisible();
  const history = page.getByRole("button", { name: "Conversation history", exact: true });
  await history.click();
  await expect(history).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByRole("navigation", { name: "Conversations" })).toBeVisible();
  await history.click();
  await expect(page.getByRole("navigation", { name: "Conversations" })).toBeHidden();
  const details = page.getByRole("button", { name: "Companion details", exact: true });
  await details.click();
  const panel = page.getByRole("complementary", { name: "Companion details" });
  await expect(panel.getByRole("heading", { name: "Connections" })).toBeVisible();
  await expect(panel.getByRole("heading", { name: "Activity" })).toBeVisible();
  await expect(panel.getByRole("button", { name: "Customize", exact: true })).toBeVisible();
  await expect(panel.locator(".skeleton")).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath("chat-details-light.png"), fullPage: true });
  await panel.getByRole("button", { name: "Close details" }).focus();
  await page.keyboard.press("Escape");
  await expect(panel).toHaveCount(0);
  await expect(details).toBeFocused();
  await expect(page.getByLabel("Your message")).toContainText("Draft a reply");
});

test("picker scrolls at its boundaries, saves composed choices, and restores them across surfaces", async ({ page }) => {
  await mkdir(".visual-check", { recursive: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.emulateMedia({ colorScheme: "light", reducedMotion: "reduce" });
  let profile = { name: "Moss", avatar_seed: "v2:c=slate;h=none;p=none", created_at: "2026-09-30", paused: false, style_preset: "warm" };
  const writes: unknown[] = [];
  await page.route(/\/v1\/companion(?:\/(?:avatar|rename))?$/, async route => {
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON();
      writes.push(body);
      profile = { ...profile, ...body };
    }
    await route.fulfill({ json: profile });
  });
  await page.goto("/companion");
  await page.getByRole("button", { name: "Customize", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Customize your companion" });
  const box = await dialog.boundingBox();
  expect(box?.width).toBe(1200);
  expect(box?.height).toBe(720);
  await dialog.screenshot({ path: ".visual-check/dialog-plain.png" });
  const colors = dialog.getByRole("radiogroup", { name: "Colors" });
  await expect(dialog.getByRole("button", { name: "Previous colors" })).toBeDisabled();
  await dialog.getByRole("button", { name: "Next colors" }).click();
  await expect.poll(() => colors.evaluate(element => element.scrollLeft)).toBeGreaterThan(0);
  await expect(dialog.getByRole("radio", { name: "Color: Slate" })).toBeChecked();
  await dialog.getByRole("radio", { name: "Color: Slate" }).focus();
  await page.keyboard.press("End");
  await expect(dialog.getByRole("radio", { name: "Color: Sand" })).toBeFocused();
  await expect(dialog.getByRole("button", { name: "Next colors" })).toBeDisabled();
  await page.keyboard.press("Home");
  await expect(dialog.getByRole("button", { name: "Previous colors" })).toBeDisabled();
  for (let step = 0; step < 5; step++) await page.keyboard.press("ArrowRight");
  const rose = dialog.getByRole("radio", { name: "Color: Rose" });
  await expect(rose).toBeFocused();
  const roseBox = await rose.boundingBox(), rowBox = await colors.boundingBox();
  expect(roseBox!.x + roseBox!.width).toBeLessThanOrEqual(rowBox!.x + rowBox!.width);
  await dialog.getByRole("radio", { name: "Color: Jade" }).click();
  await dialog.getByRole("radio", { name: "Character: Mint gumdrop" }).click();
  await dialog.getByRole("radio", { name: "Pet: Fox" }).focus();
  await page.keyboard.press("Space");
  await expect(dialog.getByRole("radio", { name: "Pet: Fox" })).toBeChecked();
  await dialog.getByRole("button", { name: "Rename companion" }).click();
  await dialog.getByRole("textbox", { name: "Companion name" }).fill("Fern");
  await page.keyboard.press("Enter");
  await dialog.screenshot({ path: ".visual-check/dialog-composed.png" });
  await dialog.getByRole("button", { name: "Save", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  expect(writes).toEqual([{ name: "Fern" }, { avatar_seed: "v2:c=jade;h=gumdrop;p=fox" }]);
  await page.reload();
  await expect(page.getByRole("img", { name: "Fern's avatar" }).locator('[data-character="gumdrop"]')).toHaveCount(1);
  await page.getByRole("button", { name: "Customize", exact: true }).click();
  for (const name of ["Color: Jade", "Character: Mint gumdrop", "Pet: Fox"]) await expect(dialog.getByRole("radio", { name })).toBeChecked();
  await page.keyboard.press("Escape");
  await page.goto("/chat");
  await expect(page.locator(".chat-identity [data-pet='fox']")).toHaveCount(1);
  await page.getByRole("button", { name: "Companion details", exact: true }).click();
  await expect(page.locator(".details-profile [data-character='gumdrop']")).toHaveCount(1);
  await page.getByRole("button", { name: "Customize", exact: true }).click();
  await dialog.getByRole("radio", { name: "Pet: Cat" }).click();
  await dialog.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.locator(".chat-identity [data-pet='cat']")).toHaveCount(1);
});

test("companion customization previews without saving and keeps keyboard focus contained", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/companion");
  const opener = page.getByRole("button", { name: "Customize", exact: true });
  await opener.click();
  const dialog = page.getByRole("dialog", { name: "Customize your companion" });
  const saves: string[] = [];
  page.on("request", request => { if (request.method() === "POST") saves.push(request.url()); });
  await dialog.getByRole("button", { name: "Rename companion" }).click();
  await dialog.getByLabel("Companion name").fill("Fern");
  await dialog.getByLabel("Companion name").press("Enter");
  await dialog.getByRole("radio", { name: "Character: Mint gumdrop" }).click();
  await expect(dialog.getByRole("radio", { name: "Character: Mint gumdrop" })).toHaveAttribute("aria-checked", "true");
  await expect(dialog.getByRole("region", { name: "Companion preview" }).getByRole("heading", { name: "Fern" })).toBeVisible();
  await dialog.getByRole("button", { name: "Save", exact: true }).focus();
  await page.keyboard.press("Tab");
  await expect(dialog.getByRole("button", { name: "Close dialog" })).toBeFocused();
  await page.screenshot({ path: testInfo.outputPath("companion-editor-light.png"), fullPage: true });
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(opener).toBeFocused();
  expect(saves).toEqual([]);
});

for (const theme of ["light", "dark"] as const) {
  test(`${theme} screens and editor fit a 390px phone`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
    const errors: string[] = [];
    page.on("pageerror", error => errors.push(error.message));
    for (const route of ["onboarding", "chat", "companion", "connections", "activity", "memory", "rules", "usage", "settings"]) {
      await page.goto(`/${route}`);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await expect(page.locator("main .skeleton")).toHaveCount(0);
      await page.screenshot({ path: testInfo.outputPath(`${route}-${theme}-phone.png`), fullPage: true });
      const overflow = await page.evaluate(() => [...document.querySelectorAll("body *")].filter(element => {
        const rect = element.getBoundingClientRect();
        return rect.width > 0 && rect.right > innerWidth + 1 && !element.closest('[hidden], .avatar-options, .tab-list');
      }).map(element => `${element.tagName}.${element.className}`));
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${route}: ${overflow.join(", ")}`).toBe(true);
    }
    await page.goto("/companion");
    await page.getByRole("button", { name: "Customize", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Customize your companion" });
    await expect(dialog.getByRole("button", { name: "Save", exact: true })).toBeVisible();
    await expect(dialog.getByRole("button", { name: "Save", exact: true })).toBeInViewport();
    expect(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath(`editor-${theme}-phone.png`) });
    expect(errors).toEqual([]);
  });
}
