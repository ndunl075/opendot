import { expect, test } from "@playwright/test";

test("activity evidence links navigate without reloading and scroll/focus the hashed rule and memory", async ({ page }) => {
  await page.route("**/v1/onboarding", route => route.fulfill({ json: { current_step: "done", completed_steps: ["done"] } }));
  await page.route("**/v1/activity?*", route => route.fulfill({ json: { items: [{ id: "e", at: "2026-09-30", kind: "action", title: "Draft prepared", why: "Requested", rule_id: "rule/target", rule_name: "Draft rule", memory_ids: ["memory/target"] }] } }));
  await page.route("**/v1/rules", route => route.fulfill({ json: { rules: Array.from({ length: 20 }, (_, i) => ({ id: i === 19 ? "rule/target" : `rule-${i}`, name: `Rule ${i}`, action: "draft", behavior: "ask", created_at: "2026-09-30" })) } }));
  await page.route("**/v1/memory?*", route => route.fulfill({ json: { total: 1, items: [{ id: "memory/target", statement: "Prefers drafts", source: "chat", source_label: "Chat", confidence: "confirmed", learned_at: "2026-09-30" }] } }));
  await page.goto("/activity");
  const documents: string[] = [];
  page.on("request", request => { if (request.resourceType() === "document") documents.push(request.url()); });
  await page.getByRole("link", { name: "Rule: Draft rule" }).click();
  const rule = page.getByRole("article", { name: "Rule 19", exact: true });
  await expect(rule).toBeFocused(); await expect(rule).toBeInViewport();
  await page.getByRole("link", { name: "Activity", exact: true }).click();
  await page.getByRole("link", { name: "Memory memory/target" }).click();
  await expect(page.getByRole("article", { name: "Memory: Prefers drafts" })).toBeFocused();
  expect(documents).toEqual([]);
});
