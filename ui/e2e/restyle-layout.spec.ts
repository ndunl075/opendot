import { expect, test } from "@playwright/test";
import { mkdir } from "node:fs/promises";

test.beforeEach(async ({ page }) => {
  await mkdir(".visual-check", { recursive: true });
  await page.route("**/v1/onboarding", async route => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...await response.json(), current_step: "done", completed_steps: ["done"] } });
  });
  await page.route("**/v1/companion", route => route.fulfill({ json: {
    name: "Juniper", avatar_seed: "v2:c=slate;h=none;p=none", created_at: "2026-09-30", paused: false, style_preset: "warm",
  } }));
});

// Browser zoom reduces the CSS viewport and increases the device scale. Keeping
// the physical output at 1920x1080 exercises the same layout at 100, 125 and 150%.
for (const size of [
  { name: "1080p-100", width: 1920, height: 1080, scale: 1 },
  { name: "1080p-125", width: 1536, height: 864, scale: 1.25 },
  { name: "1080p-150", width: 1280, height: 720, scale: 1.5 },
  { name: "laptop", width: 1366, height: 768, scale: 1 },
  { name: "phone", width: 390, height: 844, scale: 1 },
  { name: "short-phone", width: 390, height: 600, scale: 1 },
]) {
  test.describe(size.name, () => {
    test.use({ viewport: { width: size.width, height: size.height }, deviceScaleFactor: size.scale });
    for (const theme of ["light", "dark"] as const) {
      test(`customization fits with a fixed preview and ${theme} rings`, async ({ page }) => {
        await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
        await page.goto("/companion");
        await page.getByRole("button", { name: "Customize", exact: true }).click();
        const dialog = page.getByRole("dialog", { name: "Customize your companion" });
        await page.evaluate(() => document.fonts.ready);
        const box = (await dialog.boundingBox())!;
        expect(box.y).toBeGreaterThanOrEqual(16);
        expect(box.y + box.height).toBeLessThanOrEqual(size.height - 16);
        expect(await dialog.evaluate(element => element.scrollHeight <= element.clientHeight)).toBe(true);
        expect(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
        const save = dialog.getByRole("button", { name: "Save", exact: true });
        await expect(save).toBeInViewport({ ratio: 1 });
        const options = dialog.locator(".identity-options");
        const optionsBox = (await options.boundingBox())!;
        if (size.width > 800) {
          const petsBox = (await dialog.getByRole("radiogroup", { name: "Pets" }).boundingBox())!;
          expect(petsBox.y + petsBox.height).toBeLessThanOrEqual(optionsBox.y + optionsBox.height);
          expect(await options.evaluate(element => element.scrollHeight <= element.clientHeight)).toBe(true);
        }
        expect(await page.evaluate(() => getComputedStyle(document.body).overflow)).toBe("hidden");
        const rename = (await dialog.getByRole("button", { name: "Rename companion" }).boundingBox())!;
        const name = (await dialog.getByRole("heading", { name: "Juniper" }).boundingBox())!;
        expect(rename.x - name.x - name.width).toBeCloseTo(12, 0);
        expect(rename.width).toBe(32);
        expect(rename.height).toBe(32);
        const swatch = (await dialog.locator(".avatar-swatch").first().boundingBox())!;
        expect(swatch.width).toBeGreaterThanOrEqual(64);
        expect(swatch.width).toBeLessThanOrEqual(96);
        await expect(dialog.locator('.identity-avatar [data-avatar-base="ring"]')).toHaveCount(1);
        await page.mouse.move(0, 0);
        await page.screenshot({ path: `.visual-check/customize-${size.name}-${theme}.png` });
        await dialog.getByRole("radio", { name: "Character: Mint gumdrop" }).click();
        await expect(dialog.locator('.identity-avatar [data-avatar-base="disc"]')).toHaveCount(1);
        await expect(dialog.locator('.identity-avatar [data-ring-center]')).toHaveCount(0);
        const before = await save.boundingBox();
        await options.evaluate(element => { element.scrollTop = element.scrollHeight; });
        await expect(save).toBeInViewport({ ratio: 1 });
        expect(await save.boundingBox()).toEqual(before);
        await page.mouse.move(0, 0);
        await page.screenshot({ path: `.visual-check/composed-${size.name}-${theme}.png` });
      });
    }
  });
}

for (const theme of ["light", "dark"] as const) {
  test(`${theme} screen gallery and chat keep the composer inside the viewport`, async ({ page }) => {
    await page.setViewportSize({ width: 1366, height: 768 });
    await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
    const errors: string[] = [];
    page.on("pageerror", error => errors.push(error.message));
    let onboardingStep = "done";
    await page.route("**/v1/onboarding", async route => {
      const response = await route.fetch();
      await route.fulfill({ response, json: { ...await response.json(), current_step: onboardingStep, completed_steps: onboardingStep === "done" ? ["done"] : [] } });
    });
    for (const route of ["onboarding", "companion", "activity", "memory", "rules", "connections", "usage", "settings"]) {
      onboardingStep = route === "onboarding" ? "companion" : "done";
      await page.goto(`/${route}`);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await expect(page.locator("main .skeleton")).toHaveCount(0);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.screenshot({ path: `.visual-check/${route}-${theme}.png`, fullPage: true });
      await page.screenshot({ path: `.visual-check/${route}-${theme}-viewport.png` });
    }
    const messages = [
      { id: "a1", role: "assistant", text: "You can start with a short introduction. Explain what you’re building and why this person’s experience would be helpful.\n\nThere’s time to get to know each other before you need a recommendation.", created_at: "2026-10-01T19:10:00Z" },
      { id: "u1", role: "user", text: "I want the presentation to feel more considered. Can we give the product screenshots more room and keep the copy short?", created_at: "2026-10-01T19:11:00Z" },
      { id: "a2", role: "assistant", text: "Yes. I’d open with the product in use, then let each slide make one clear point. The screenshots can carry much of the explanation.\n\nWe can use a quieter palette while keeping the layouts expressive.", created_at: "2026-10-01T19:12:00Z" },
      { id: "u2", role: "user", text: "That sounds closer. Let’s try the opening slide first.", created_at: "2026-10-01T19:13:00Z" },
      { id: "a3", role: "assistant", text: "I’ll put the main product view at the center and keep the headline to one sentence.", created_at: "2026-10-01T19:13:10Z" },
    ];
    // This gallery uses a saved transcript. Suppress the mock's automatic replay
    // so a parallel chat test cannot append unrelated demo approval messages.
    await page.routeWebSocket("**/v1/chat/stream", () => {});
    await page.route("**/v1/chat/conversations", route => route.fulfill({ json: { conversations: [{ id: "visual", title: "Presentation direction", message_count: messages.length }] } }));
    await page.route("**/v1/chat/conversations/visual", route => route.fulfill({ json: { id: "visual", title: "Presentation direction", messages } }));
    await page.goto("/chat");
    await page.getByRole("button", { name: "Conversation history" }).click();
    await page.getByRole("button", { name: /Presentation direction/ }).click();
    await expect(page.getByLabel("Your message")).toHaveCount(2);
    await page.getByRole("button", { name: "Conversation history" }).click();
    await expect(page.getByLabel("Message read time")).toHaveCount(1);
    await expect(page.getByRole("button", { name: "Voice input unavailable" })).toBeDisabled();
    for (const viewport of [{ width: 1920, height: 1080 }, { width: 1366, height: 768 }, { width: 1280, height: 720 }, { width: 390, height: 844 }]) {
      await page.setViewportSize(viewport);
      await expect(page.locator(".chat-composer")).toBeInViewport({ ratio: 1 });
      await expect(page.getByLabel("Companion reply").last()).toBeInViewport({ ratio: 1 });
      const layout = await page.evaluate(() => ({
        height: innerHeight, scrollHeight: document.documentElement.scrollHeight,
        outside: [...document.querySelectorAll("body *")].filter(element => element.getBoundingClientRect().bottom > innerHeight).map(element => ({ tag: element.tagName, class: String(element.className), top: element.getBoundingClientRect().top, bottom: element.getBoundingClientRect().bottom })),
        overflowing: [...document.querySelectorAll(".app-shell, .sidebar, .navigation-panel, .sidebar-bottom, .workspace, .workspace-footer")].map(element => ({
          class: element.className, top: element.getBoundingClientRect().top, bottom: element.getBoundingClientRect().bottom, height: element.getBoundingClientRect().height,
        })),
      }));
      expect(layout.scrollHeight, JSON.stringify({ viewport, ...layout })).toBeLessThanOrEqual(layout.height);
      await page.mouse.move(0, 0);
      await page.screenshot({ path: `.visual-check/chat-${viewport.width}-${theme}.png` });
    }
    await page.locator(".transcript").evaluate(element => { element.scrollTop = 0; });
    await expect.poll(() => page.locator(".transcript").evaluate(element => element.scrollTop)).toBe(0);
    await page.setViewportSize({ width: 390, height: 760 });
    await expect(page.getByLabel("Companion reply").first()).toBeInViewport();
    await expect.poll(() => page.locator(".transcript").evaluate(element => element.scrollTop)).toBe(0);
    expect(errors).toEqual([]);
  });
}
