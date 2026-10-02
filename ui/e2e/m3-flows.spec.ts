import { expect, test, type Page, type Route } from "@playwright/test";

const doneSteps = ["companion", "chatgpt", "weekly_limit", "connections", "intro", "done"];

function captureUnexpectedErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("console", message => {
    if (message.type() === "error") errors.push(message.text());
  });
  return errors;
}

async function mockCompletedOnboarding(page: Page) {
  await page.route(/\/v1\/onboarding(?:\/|$)/, async route => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...await response.json(), current_step: "done", completed_steps: doneSteps } });
  });
}

async function mockApprovalStream(page: Page) {
  const base = { conversation_id: "conv_e2e", message_id: "message_e2e" };
  const approval = { id: "approval_e2e", ...base, title: "Draft a venue reply", action: "gmail.create_draft", status: "pending", created_at: "2026-09-30T12:00:00Z", payload: { body: "Hello" }, preview: "A draft will be saved, not sent.", review_note: "The reviewer confirmed this is a draft only.", review_verdict: "ok", rule_suggestion: "Always allow venue drafts" };
  await page.route("**/v1/chat/messages", route => route.fulfill({ json: { ...base, message_id: "user_e2e", stream_path: "/v1/chat/stream" } }));
  await page.route("**/v1/approvals/approval_e2e", route => route.fulfill({ json: approval }));
  await page.routeWebSocket("**/v1/chat/stream", socket => {
    socket.onMessage(message => {
      const frame = JSON.parse(String(message));
      if (frame.type !== "resume" || frame.conversation_id !== base.conversation_id) return;
      const events = [
        { type: "message_started", seq: 1, ...base },
        { type: "text_delta", seq: 2, text: "I can draft that ", ...base },
        { type: "text_delta", seq: 3, text: "reply for you.", ...base },
        { type: "approval_required", seq: 4, approval, ...base },
        { type: "completed", seq: 5, text: "I can draft that reply for you.", usage: { model: "mock-model-terra", effort: "low", credits: 0.12, input_tokens: 812, output_tokens: 64, cached_input_tokens: 640 }, ...base }
      ];
      for (const event of events) socket.send(JSON.stringify(event));
    });
  });
}

async function approvalResponse(route: Route, status: "approved" | "denied" = "approved", createdRule = false) {
  const response = await route.fetch();
  const result = await response.json();
  await route.fulfill({
    response,
    json: { ...result, approval: { ...result.approval, status }, executed: status === "approved", result_summary: createdRule ? "Rule created for future drafts." : status === "approved" ? "Draft approved." : "Draft denied.", ...(createdRule ? { created_rule_id: "rule-created" } : {}) }
  });
}

test("onboarding names a companion, acknowledges plan limits, skips connections, and opens chat", async ({ page }) => {
  const errors = captureUnexpectedErrors(page);
  let state = "companion";
  await page.route(/\/v1\/onboarding(?:\/|$)/, async route => {
    const request = route.request();
    if (request.method() === "POST" && request.url().endsWith("/companion")) state = "chatgpt";
    if (request.method() === "POST" && request.url().endsWith("/acknowledge")) state = "connections";
    if (request.method() === "POST" && request.url().endsWith("/complete")) state = "done";
    const response = await route.fetch();
    const data = await response.json();
    const complete = state === "done";
    await route.fulfill({ response, json: { ...data, current_step: state, companion_name: "Cedar", avatar_seed: "open-fold-2", completed_steps: complete ? doneSteps : state === "connections" ? ["companion", "chatgpt", "weekly_limit"] : state === "chatgpt" ? ["companion"] : [], chatgpt: { ...data.chatgpt, state: state === "chatgpt" ? "signed_out" : "signed_in", plan: "eligible_plus", eligible: true, credits_enabled: false }, intro_message: "Hello from Cedar." } });
  });
  await page.route("**/v1/auth/chatgpt/start", async route => {
    await route.fulfill({ json: { state: "pending", authorize_url: "https://example.test/sign-in", expires_at: "2099-01-01T00:00:00Z", poll_interval_seconds: 1 } });
  });
  await page.route("**/v1/auth/chatgpt/status", async route => {
    await route.fulfill({ json: { state: "signed_in", plan: "eligible_plus", eligible: true, plan_label: "Using ChatGPT plan (Plus)", credits_enabled: false, manage_usage_url: "https://chatgpt.com/#settings/Usage" } });
  });
  await page.goto("/onboarding");
  await page.getByLabel("Companion name").fill("Cedar");
  const avatar = page.getByRole("img", { name: "Selected companion avatar" });
  const firstAvatar = await avatar.innerHTML();
  await page.getByRole("radio", { name: "Color: Sky" }).click();
  expect(await avatar.innerHTML()).not.toBe(firstAvatar);
  await page.getByRole("radio", { name: "Pet: Fox" }).click();
  await page.getByRole("button", { name: "Save companion" }).click();
  await page.getByRole("button", { name: "Continue with ChatGPT" }).click();
  await expect(page.getByRole("heading", { name: "Keep usage in your hands" })).toBeVisible();
  await page.getByRole("checkbox", { name: "I set a weekly OpenDot limit" }).check();
  await page.getByRole("checkbox", { name: "I kept ChatGPT credit use off" }).check();
  await page.getByRole("button", { name: "Acknowledge usage settings" }).click();
  await expect(page.getByRole("heading", { name: "Connect your apps" })).toBeVisible();
  await page.getByRole("button", { name: "Continue to introduction" }).click();
  await expect(page.getByText("Hello from Cedar.")).toBeVisible();
  await page.getByRole("button", { name: "Open chat" }).click();
  await expect(page).toHaveURL(/\/chat$/);
  await expect(page.getByRole("textbox", { name: "Message your companion" })).toBeVisible();
  expect(errors).toEqual([]);
});

test("chat streams an approval and sends approve, deny, and always-allow decisions", async ({ page }) => {
  const errors = captureUnexpectedErrors(page);
  await mockCompletedOnboarding(page);
  await mockApprovalStream(page);
  await page.route("**/v1/approvals/*/approve", route => approvalResponse(route));
  await page.route("**/v1/approvals/*/deny", route => approvalResponse(route, "denied"));
  await page.route("**/v1/approvals/*/always-allow", route => approvalResponse(route, "approved", true));
  await page.goto("/chat");
  await page.getByRole("textbox", { name: "Message your companion" }).fill("Draft a reply to the venue");
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(page.getByRole("log")).toContainText("I can draft that reply for you.");
  const card = page.getByLabel(/^Approval:/);
  await expect(page.getByRole("region", { name: "Reviewer note" })).toBeVisible();
  await expect(page.getByLabel("Companion reply", { exact: true })).toHaveAttribute("aria-busy", "false");
  await expect(page.getByText("mock-model-terra")).toHaveCount(0);
  await expect(page.getByText("low effort")).toHaveCount(0);
  await expect(page.getByText("0.12 credits")).toHaveCount(0);
  const approve = page.waitForRequest(request => request.url().includes("/approve") && request.method() === "POST");
  await card.getByRole("button", { name: "Approve" }).click();
  await approve;
  await expect(card).toContainText("approved");

  await page.reload();
  await page.getByRole("textbox", { name: "Message your companion" }).fill("Try another draft");
  await page.getByRole("button", { name: "Send message" }).click();
  const fresh = page.getByLabel(/^Approval:/);
  await expect(fresh).toBeVisible();
  const deny = page.waitForRequest(request => request.url().includes("/deny") && request.method() === "POST");
  await fresh.getByRole("button", { name: "Deny" }).click();
  await deny;
  await expect(fresh).toContainText("denied");

  await page.reload();
  await page.getByRole("textbox", { name: "Message your companion" }).fill("One more draft");
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(fresh).toBeVisible();
  await fresh.getByRole("button", { name: "Always allow this" }).click();
  const always = page.waitForRequest(request => request.url().includes("/always-allow") && request.method() === "POST");
  await page.getByRole("button", { name: "Approve and create rule" }).click();
  await always;
  await expect(fresh).toContainText("Rule created for future drafts.");
  expect(errors).toEqual([]);
});

test("rules support create, edit, delete, and show core deny rules as locked", async ({ page }) => {
  const errors = captureUnexpectedErrors(page);
  await mockCompletedOnboarding(page);
  await page.goto("/rules");
  for (const name of ["Ask before drafting email", "Create calendar events from my own invites", "Read my inbox", "Hand off purchases to me"]) await expect(page.getByRole("article", { name })).toBeVisible();
  const locked = page.getByRole("article", { name: "Never send money" });
  await expect(locked).toContainText("Locked");
  await expect(locked.getByRole("button")).toHaveCount(0);
  await page.getByRole("button", { name: "Create rule" }).click();
  await page.getByLabel("Rule name").fill("Venue drafts");
  await page.getByLabel("Action").fill("gmail.create_draft");
  const create = page.waitForRequest(request => request.url().endsWith("/v1/rules") && request.method() === "POST");
  await page.getByRole("button", { name: "Save rule" }).click();
  await create;
  const editable = page.getByRole("article", { name: "Ask before drafting email" });
  await editable.getByRole("button", { name: "Edit Ask before drafting email" }).click();
  await page.getByRole("combobox", { name: "Behavior" }).click();
  await page.getByRole("option", { name: "Only when preapproved (auto_if_preapproved)", exact: true }).click();
  const edit = page.waitForRequest(request => /\/v1\/rules\/rule_001$/.test(request.url()) && request.method() === "PUT");
  await page.getByRole("button", { name: "Save rule" }).click();
  await edit;
  await editable.getByRole("button", { name: "Delete Ask before drafting email" }).click();
  const remove = page.waitForRequest(request => /\/v1\/rules\/rule_001$/.test(request.url()) && request.method() === "DELETE");
  await page.getByRole("dialog").getByRole("button", { name: "Delete rule" }).click();
  await remove;
  expect(errors).toEqual([]);
});

test("usage shows all breakdowns, validates budgets, handles 501, and chat works in light and dark themes by keyboard", async ({ page }) => {
  const errors = captureUnexpectedErrors(page);
  await mockCompletedOnboarding(page);
  await mockApprovalStream(page);
  await page.goto("/usage");
  for (const label of ["Draft the venue reply", "Morning brief", "chat", "routine"]) await expect(page.getByText(label, { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("link", { name: "Manage usage" })).toBeVisible();
  await page.getByLabel("Per-task budget (credits)").fill("2.5");
  await page.getByLabel("Daily budget (credits)").fill("8");
  const budget = page.waitForRequest(request => request.url().endsWith("/v1/usage/budgets") && request.method() === "PUT");
  await page.getByRole("button", { name: "Save budgets" }).click();
  expect(JSON.parse((await budget).postData() ?? "{}")).toEqual({ task_credits: 2.5, daily_credits: 8, daily_hard_stop: true });
  await page.route("**/v1/usage?days=7", route => route.fulfill({ status: 501, contentType: "application/json", body: JSON.stringify({ code: "not_implemented", message: "Unavailable" }) }));
  await page.reload();
  await expect(page.getByText("Not available in this version yet")).toBeVisible();

  await page.unroute("**/v1/usage?days=7");
  for (const theme of ["light", "dark"]) {
    await page.goto("/settings/general");
    await page.getByRole("combobox", { name: "Appearance" }).click();
  await page.getByRole("option", { name: theme === "light" ? "Light" : "Dark", exact: true }).click();
    await page.goto("/chat");
    await expect(page.getByRole("textbox", { name: "Message your companion" })).toBeVisible();
    await page.keyboard.press("Tab");
    await page.getByRole("textbox", { name: "Message your companion" }).focus();
    await page.keyboard.type("Keyboard draft");
    await page.keyboard.press("Enter");
    const card = page.getByLabel(/^Approval:/);
    await expect(card).toBeVisible();
    await card.getByRole("button", { name: "Approve" }).focus();
    await expect(card.getByRole("button", { name: "Approve" })).toBeFocused();
  }
  expect(errors.filter(error => !error.includes("501 (Not Implemented)"))).toEqual([]);
});
