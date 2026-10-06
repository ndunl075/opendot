import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  // Completion in another test or preview tab must not change this test's start.
  await page.route("**/v1/onboarding", async route => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...await response.json(), current_step: "connections", completed_steps: ["companion", "chatgpt", "weekly_limit"] } });
  });
});

test("all routes, About copy, self-hosted fonts, and persisted OS/override themes", async ({ page }) => {
  // Only mark setup complete for this
  // navigation/design test; every screen still reads the real contract mock.
  await page.route("**/v1/onboarding", async route => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...await response.json(), current_step: "done", completed_steps: ["companion", "chatgpt", "weekly_limit", "connections", "intro", "done"] } });
  });
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(page).toHaveURL(/\/chat$/);
  await expect(page.getByText("Connected", { exact: true })).toBeVisible();
  await expect(page.locator("footer")).toHaveCount(0);
  await expect(page.getByText("Daemon connected", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("combobox", { name: "Appearance" })).toHaveCount(0);
  await page.getByRole("navigation", { name: "Main navigation" }).getByRole("link", { name: "Settings" }).click();
  await page.getByRole("combobox", { name: "Appearance" }).click();
  await page.getByRole("option", { name: "Light", exact: true }).click();
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.getByRole("navigation", { name: "Main navigation" }).getByRole("link", { name: "Chat" }).click();
  await expect(page.getByRole("button", { name: "Send message", exact: true })).toBeVisible();
  await page.getByRole("navigation", { name: "Main navigation" }).getByRole("link", { name: "Settings" }).click();
  for (const name of ["General", "Companion", "Connections", "Memory", "Rules", "Usage", "Activity"]) {
    await page.getByRole("navigation", { name: "Settings sections" }).getByRole("link", { name, exact: true }).click();
    await expect(page.getByRole("heading", { name, level: 1 })).toBeVisible();
    const markers: Record<string, string> = { General: "Save general", Companion: "Rename", Activity: "Refresh activity", Rules: "Create rule", Memory: "Search", Connections: "Connect Gmail", Usage: "Save budgets" };
    await expect(page.getByRole("button", { name: markers[name], exact: true })).toBeVisible();
    await expect(page.getByRole("heading", { name, level: 1 })).toBeFocused();
  }
  await page.getByRole("link", { name: "About", exact: true }).click();
  await expect(page.getByText("OpenDot is an independent open-source project. It is not affiliated with, endorsed by, or sponsored by OpenAI or Anthropic. ChatGPT is a trademark of OpenAI.")).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "About", level: 1 })).toBeVisible();
  await page.getByRole("link", { name: "General", exact: true }).click();
  await page.getByRole("combobox", { name: "Appearance" }).click();
  await page.getByRole("option", { name: "System", exact: true }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.emulateMedia({ colorScheme: "light" });
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.evaluate(() => document.fonts.ready);
  expect(await page.evaluate(() => document.fonts.check('16px "Inter Variable"'))).toBe(true);
  expect(await page.locator("body").evaluate(element => getComputedStyle(element).fontFamily)).toContain("Inter Variable");
  const fonts = await page.evaluate(() => performance.getEntriesByType("resource").map(entry => entry.name).filter(name => /\.woff2?/.test(name)));
  expect(fonts.length).toBeGreaterThan(0);
  expect(fonts.every(url => new URL(url).origin === new URL(page.url()).origin)).toBe(true);
  expect(errors).toEqual([]);
});

test("unfinished setup redirects to onboarding; mock chat streams a reply without inline usage and approval", async ({ page }) => {
  await page.goto("/chat");
  await expect(page).toHaveURL(/\/onboarding$/);
  await expect(page.getByRole("button", { name: "Continue to introduction" })).toBeVisible();
  await page.route("**/v1/onboarding", async route => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...await response.json(), current_step: "done" } });
  });
  await page.goto("/chat");
  await expect(page.getByText("Connected", { exact: true })).toBeVisible();
  await page.getByLabel("Message your companion").fill("Draft a reply");
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(page.getByLabel("Companion reply", { exact: true })).toHaveAttribute("aria-busy", "false");
  await expect(page.getByText("mock-model-terra", { exact: true })).toHaveCount(0);
  await expect(page.getByText("0.12 credits", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Approve", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Always allow this", exact: true })).toBeVisible();
});

test("screens fit 720px in both themes without runtime errors", async ({ page }, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.setViewportSize({ width: 720, height: 1000 });
  await page.route("**/v1/onboarding", async route => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...await response.json(), current_step: "done" } });
  });
  for (const theme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: theme });
    for (const path of ["chat", "companion", "activity", "rules", "memory", "connections", "usage", "settings", "settings/general"]) {
      await page.goto(`/${path}`);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await expect(page.locator("main .skeleton")).toHaveCount(0);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      expect(await page.locator("vite-error-overlay").count()).toBe(0);
      if (["chat", "rules", "settings"].includes(path)) await page.screenshot({ path: testInfo.outputPath(`${path}-${theme}.png`), fullPage: true });
    }
  }
  expect(errors).toEqual([]);
});

test("mobile navigation is keyboard usable and fits a narrow screen", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 740 });
  // Exercise tab order on a settled route; the setup redirect is tested above.
  await page.goto("/onboarding");
  await expect(page.getByRole("button", { name: "Continue to introduction" })).toBeVisible();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to content" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("main")).toBeFocused();
  const toggle = page.getByRole("button", { name: "Open navigation" });
  await toggle.focus(); await page.keyboard.press("Enter");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Chat", exact: true })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(toggle).toBeFocused();
  await toggle.click();
  await page.getByRole("link", { name: "Settings", exact: true }).click();
  await expect(page.getByRole("navigation", { name: "Main navigation" })).toBeHidden();
  await expect(page).toHaveURL(/\/onboarding$/);
  await expect(page.getByRole("heading", { name: "Welcome to OpenDot" })).toBeVisible();
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
