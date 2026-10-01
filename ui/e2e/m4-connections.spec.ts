import { expect, test } from "@playwright/test";

for (const surface of ["connections", "onboarding"] as const) {
  test(`${surface}: set up Google and connect Gmail and Calendar in an external tab`, async ({ page, context }, testInfo) => {
    if (surface === "onboarding") await page.setViewportSize({ width: 390, height: 844 });
    const errors: string[] = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.route(/\/v1\/onboarding(?:\/|$)/, async route => {
      const response = await route.fetch();
      const data = await response.json();
      await route.fulfill({ response, json: { ...data, current_step: surface === "onboarding" ? "connections" : "done", completed_steps: surface === "onboarding" ? ["companion", "chatgpt", "weekly_limit"] : ["done"], chatgpt: { ...data.chatgpt, state: "signed_in", eligible: true, plan: "eligible_plus", credits_enabled: false } } });
    });
    let configured = false;
    const connected = new Set<string>();
    await page.route("**/v1/connections/google/client", async route => {
      if (route.request().method() === "PUT") {
        expect(route.request().postDataJSON()).toEqual({ client_id: "demo.apps.googleusercontent.com", client_secret: "synthetic-client-secret" });
        configured = true;
      }
      await route.fulfill({ json: { configured, setup_steps: ["Create a Google project.", "Enable Gmail and Calendar APIs.", "Create a Desktop app OAuth client."] } });
    });
    await page.route("**/v1/connections/start", async route => {
      const { app } = route.request().postDataJSON();
      expect(configured).toBe(true);
      expect(["gmail", "google_calendar"]).toContain(app);
      await route.fulfill({ json: { app, state: `state-${app}`, authorize_url: `https://example.test/google-consent/${app}` } });
    });
    await page.route("**/v1/connections", route => route.fulfill({ json: { connections: [...connected].map(app => ({ id: app, app, account_label: "demo@example.test", health: "ok", read_only: true, write_opt_in: false, write_opt_in_available: true })) } }));
    await context.route("https://example.test/google-consent/**", route => route.fulfill({ contentType: "text/html", body: "<title>Mock Google consent</title><h1>Mock Google consent</h1>" }));
    await page.goto(`/${surface}`);
    await expect(page.getByRole("heading", { name: "Set up Google" })).toBeVisible();
    await expect(page.getByRole("listitem").filter({ hasText: "Create a Desktop app OAuth client." })).toBeVisible();
    await expect(page.getByRole("link", { name: "Open Google Cloud Console" })).toHaveAttribute("rel", "noopener noreferrer");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath("google-setup.png"), fullPage: true });
    await page.getByLabel("Google client ID").fill("demo.apps.googleusercontent.com");
    await page.getByLabel("Google client secret").fill("synthetic-client-secret");
    await page.getByRole("button", { name: "Save Google client" }).click();
    await expect(page.getByLabel("Google client secret")).toHaveCount(0);
    expect(await page.content()).not.toContain("synthetic-client-secret");
    for (const [app, label] of [["gmail", "Gmail"], ["google_calendar", "Google Calendar"]]) {
      const popupPromise = context.waitForEvent("page");
      await page.getByRole("button", { name: `Connect ${label}`, exact: true }).click();
      const popup = await popupPromise;
      await expect(popup).toHaveURL(`https://example.test/google-consent/${app}`);
      expect(await popup.evaluate(() => window.opener === null)).toBe(true);
      await expect(page.getByText(/Waiting for Google/)).toBeVisible();
      connected.add(app);
      await expect(page.getByText(`${label} is connected and healthy.`)).toBeVisible({ timeout: 10_000 });
      await popup.close();
    }
    await expect(page.getByText(/OpenDot asks Google for read-only access by default/)).toBeVisible();
    expect(errors).toEqual([]);
  });
}
