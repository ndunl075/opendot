import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  // This checkout's contract mock is stateless. Model the completion transition
  // per page, retaining the real endpoint response and all UI behavior assertions.
  await page.route("**/v1/onboarding", async route => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...await response.json(), current_step: "connections", completed_steps: ["companion", "chatgpt", "weekly_limit"] } });
  });
  await page.route("**/v1/onboarding/complete", async route => {
    expect(route.request().method()).toBe("POST");
    const response = await route.fetch();
    expect(response.status()).toBe(200);
    await route.fulfill({ response, json: { ...await response.json(), current_step: "intro", completed_steps: ["companion", "chatgpt", "weekly_limit", "connections"] } });
  });
});

for (const action of ["Continue to introduction", "Skip for now"]) {
  test(`${action} shows pending feedback and reaches the contract-mock introduction`, async ({ page }, testInfo) => {
    let release!: () => void;
    const gate = new Promise<void>(resolve => { release = resolve; });
    await page.route("**/v1/onboarding/complete", async route => {
      await gate;
      await route.fallback();
    });
    await page.goto("/chat");
    await expect(page).toHaveURL(/\/onboarding$/);
    await expect(page.getByRole("main")).toBeFocused();
    await expect(page.getByRole("main")).toHaveCSS("outline-style", "none");
    await expect(page.getByRole("main")).toHaveCSS("border-top-width", "0px");
    const button = page.getByRole("button", { name: action, exact: true });
    await button.focus();
    await page.keyboard.press("Tab");
    await page.keyboard.press("Shift+Tab");
    await expect(button).toBeFocused();
    await expect(button).toHaveCSS("outline-style", "solid");
    await expect(button).toHaveCSS("outline-width", "3px");
    await button.click();
    try {
      for (const label of ["Continue to introduction", "Skip for now"]) {
        const pending = page.getByRole("button", { name: new RegExp(label) });
        await expect(pending).toBeDisabled();
        await expect(pending).toHaveAttribute("aria-busy", "true");
        await expect(pending.locator(".spinner")).toBeVisible();
      }
    } finally {
      release();
    }
    await expect(page.getByRole("heading", { name: /Hello, I/ })).toBeVisible();
    await expect(page.getByRole("button", { name: "Open chat" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Connect your apps" })).toHaveCount(0);
    await page.screenshot({ path: testInfo.outputPath("introduction.png"), fullPage: true });
  });

  test(`${action} shows an inline failure and can retry`, async ({ page }) => {
    await page.route("**/v1/onboarding/complete", route => route.fulfill({
      status: 503,
      json: { code: "unavailable", message: "Could not finish setup. Please try again." }
    }), { times: 1 });
    await page.goto("/onboarding");
    await page.getByRole("button", { name: action, exact: true }).click();
    await expect(page.getByRole("main").getByRole("alert")).toContainText("Could not finish setup. Please try again.");
    await expect(page.getByRole("heading", { name: "Connect your apps" })).toBeVisible();
    for (const label of ["Continue to introduction", "Skip for now"]) {
      const button = page.getByRole("button", { name: label, exact: true });
      await expect(button).toBeEnabled();
      await expect(button).not.toHaveAttribute("aria-busy");
    }
    await page.getByRole("button", { name: action, exact: true }).click();
    await expect(page.getByRole("heading", { name: /Hello, I/ })).toBeVisible();
    await expect(page.getByRole("main").getByRole("alert")).toHaveCount(0);
  });
}

test("programmatically focused headings keep the soft surface in both themes", async ({ page }, testInfo) => {
  await page.goto("/onboarding");
  const heading = page.getByRole("heading", { name: "Welcome to OpenDot" });
  for (const theme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: theme });
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    await page.keyboard.press("Tab");
    await heading.evaluate(element => { element.tabIndex = -1; element.focus(); });
    await expect(heading).toBeFocused();
    await expect(heading).toHaveCSS("outline-style", "none");
    await page.getByRole("main").focus();
    await expect(page.getByRole("main")).toHaveCSS("outline-style", "none");
    await page.screenshot({ path: testInfo.outputPath(`onboarding-${theme}.png`), fullPage: true });
  }
});
