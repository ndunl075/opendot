import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.route("**/v1/onboarding", async route => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...await response.json(), current_step: "done", completed_steps: ["done"] } });
  });
});

test("theme menu appearance, keyboard selection, dismissal and narrow viewport in both themes", async ({ page }, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto("/chat");
  await expect(page.getByText("Connected", { exact: true })).toBeVisible();
  const trigger = page.getByRole("combobox", { name: "Appearance" });
  const menu = page.getByRole("listbox", { name: "Appearance" });
  for (const theme of ["Light", "Dark"]) {
    await trigger.click(); await page.getByRole("option", { name: theme, exact: true }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme.toLowerCase());
    await trigger.focus(); await trigger.press("Space");
    await expect(trigger).toBeFocused();
    await expect(menu).toBeVisible();
    await expect(page.getByRole("option", { name: theme, exact: true })).toHaveAttribute("aria-selected", "true");
    await expect(page.getByRole("option", { name: theme, exact: true }).locator("svg")).toHaveCount(1);
    expect(await trigger.evaluate(el => getComputedStyle(el).outlineStyle)).toBe("none");
    expect(await menu.evaluate(el => ({ radius: getComputedStyle(el).borderRadius, padding: getComputedStyle(el).padding, background: getComputedStyle(el).backgroundColor }))).toEqual({ radius: "12px", padding: "8px", background: theme === "Light" ? "rgb(255, 255, 255)" : "rgb(47, 47, 47)" });
    const light = page.getByRole("option", { name: "Light", exact: true });
    await light.hover();
    expect(await light.evaluate(el => getComputedStyle(el).backgroundColor)).toBe(theme === "Light" ? "rgb(244, 244, 244)" : "rgb(56, 56, 56)");
    expect((await light.boundingBox())?.height).toBe(36);
    await page.screenshot({ path: testInfo.outputPath(`dropdown-${theme}.png`) });
    await trigger.press("Escape");
    await expect(menu).toHaveCount(0);
    await expect(trigger).toHaveText(theme);
  }
  await trigger.press("Home"); await trigger.press("ArrowDown"); await trigger.press("Enter");
  await expect(trigger).toHaveText("Light");
  await trigger.press("End"); await trigger.press("Space"); await expect(trigger).toHaveText("Dark");
  await trigger.press("s"); await trigger.press("y"); await trigger.press("Enter"); await expect(trigger).toHaveText("System");
  await trigger.click(); await page.getByLabel("Message your companion").click(); await expect(menu).toHaveCount(0);
  await trigger.press("Home"); await trigger.press("Tab"); await expect(menu).toHaveCount(0); await expect(trigger).not.toBeFocused();
  await page.setViewportSize({ width: 320, height: 740 });
  await trigger.click();
  const box = (await menu.boundingBox())!;
  expect(box.x).toBeGreaterThanOrEqual(0); expect(box.x + box.width).toBeLessThanOrEqual(320); expect(box.y + box.height).toBeLessThanOrEqual(740);
  await expect(page.locator("select")).toHaveCount(0);
  await expect(page.locator("footer")).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("dropdown stays above a modal, Escape closes it first, and selection does not submit", async ({ page }) => {
  await page.goto("/rules");
  await page.getByRole("button", { name: "Create rule", exact: true }).click();
  const dialog = page.getByRole("dialog");
  const trigger = dialog.getByRole("combobox", { name: "Behavior" });
  await trigger.click();
  await page.getByRole("option", { name: "Only when preapproved (auto_if_preapproved)", exact: true }).click();
  await expect(trigger).toHaveText("Only when preapproved (auto_if_preapproved)");
  await expect(dialog).toBeVisible();
  await trigger.press("Space"); await trigger.press("Escape");
  await expect(page.getByRole("listbox")).toHaveCount(0);
  await expect(dialog).toBeVisible(); await expect(trigger).toBeFocused();
  await trigger.press("Escape"); await expect(dialog).toHaveCount(0);
});

test("daemon failure shows a banner and recovery removes it without a footer", async ({ page }) => {
  let unhealthy = true;
  await page.route("**/v1/health", route => unhealthy ? route.abort() : route.fulfill({ json: { status: "ok" } }));
  await page.goto("/chat");
  await expect(page.locator(".connection-banner")).toHaveText("Daemon unreachable. Check the local daemon connection.");
  await expect(page.locator(".connection-banner")).toHaveAttribute("role", "alert");
  unhealthy = false;
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(page.locator(".connection-banner")).toHaveCount(0);
  await expect(page.locator("footer")).toHaveCount(0);
  await expect(page.getByText("Daemon connected", { exact: true })).toHaveCount(0);
});
