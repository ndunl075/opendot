import { expect, test } from "@playwright/test";

test("all routes, About copy, self-hosted fonts, and persisted OS/override themes", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(page.getByRole("status")).toHaveText("Daemon connected");
  await page.getByLabel("Appearance").selectOption("light");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  for (const name of ["Onboarding", "Chat", "Companion", "Activity", "Rules", "Memory", "Connections", "Usage", "Settings"]) {
    await page.getByRole("link", { name, exact: true }).click();
    await expect(page.getByRole("heading", { name, level: 1 })).toBeVisible();
    await expect(page.getByText("Coming soon", { exact: true })).toBeVisible();
    await expect(page.locator("main")).toBeFocused();
  }
  await page.getByRole("link", { name: "About", exact: true }).click();
  await expect(page.getByText("OpenDot is an independent open-source project. It is not affiliated with, endorsed by, or sponsored by OpenAI or Anthropic. ChatGPT is a trademark of OpenAI.")).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "About OpenDot" })).toBeVisible();
  await page.getByLabel("Appearance").selectOption("system");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.emulateMedia({ colorScheme: "light" });
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.evaluate(() => document.fonts.ready);
  expect(await page.evaluate(() => document.fonts.check('16px "IBM Plex Sans"'))).toBe(true);
  const fonts = await page.evaluate(() => performance.getEntriesByType("resource").map(entry => entry.name).filter(name => /\.woff2?/.test(name)));
  expect(fonts.length).toBeGreaterThan(0);
  expect(fonts.every(url => new URL(url).origin === "http://127.0.0.1:5173")).toBe(true);
  expect(errors).toEqual([]);
});

test("mobile navigation is keyboard usable and fits a narrow screen", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 740 });
  await page.goto("/");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to content" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("main")).toBeFocused();
  const toggle = page.getByRole("button", { name: "Open navigation" });
  await toggle.focus(); await page.keyboard.press("Enter");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Onboarding", exact: true })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(toggle).toBeFocused();
  await toggle.click();
  await page.getByRole("link", { name: "About", exact: true }).click();
  await expect(page.getByRole("navigation")).toBeHidden();
  await expect(page.getByRole("heading", { name: "About OpenDot" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("native dialog contains focus, blocks background, escapes, and restores focus", async ({ page }) => {
  await page.goto("/e2e/fixtures/components.html");
  const opener = page.getByRole("button", { name: "Open dialog", exact: true });
  await opener.click();
  const first = page.getByRole("button", { name: "Close dialog" });
  const last = page.getByRole("button", { name: "Last action" });
  await expect(first).toBeFocused();
  expect(await page.getByRole("dialog").evaluate(element => element.matches(":modal"))).toBe(true);
  await page.keyboard.press("Shift+Tab"); await expect(last).toBeFocused();
  await page.keyboard.press("Tab"); await expect(first).toBeFocused();
  await page.keyboard.press("Tab"); await expect(page.getByLabel("Action name")).toBeFocused();
  await page.keyboard.press("Tab"); await expect(last).toBeFocused();
  await page.keyboard.press("Escape"); await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(opener).toBeFocused();
  await page.getByRole("button", { name: "Forget memory", exact: true }).click();
  await expect(page.getByRole("button", { name: "Cancel", exact: true })).toBeFocused();
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("keyboard fields, tabs, tooltip, avatar re-roll and reduced motion", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/e2e/fixtures/components.html");
  const avatar = page.getByRole("img", { name: "Companion's woven ribbon avatar" });
  const before = await avatar.innerHTML();
  await page.getByRole("button", { name: "Re-roll avatar" }).click();
  expect(await avatar.innerHTML()).not.toBe(before);
  await page.getByRole("switch").focus(); await page.keyboard.press("Space");
  await expect(page.getByRole("switch")).toBeChecked();
  await page.getByRole("checkbox").focus(); await page.keyboard.press("Space");
  await expect(page.getByRole("checkbox")).toBeChecked();
  await page.getByRole("tab", { name: "In progress" }).focus(); await page.keyboard.press("End");
  await expect(page.getByRole("tabpanel")).toHaveText("Completed tasks");
  await page.getByRole("button", { name: "Approval help" }).focus();
  await expect(page.getByRole("tooltip")).toBeVisible();
  await page.keyboard.press("Escape"); await expect(page.getByRole("tooltip")).toHaveCount(0);
  expect(await page.locator(".spinner").evaluate(element => getComputedStyle(element).animationName)).toBe("none");
  expect(await page.locator(".skeleton").evaluate(element => getComputedStyle(element).animationName)).toBe("none");
});
