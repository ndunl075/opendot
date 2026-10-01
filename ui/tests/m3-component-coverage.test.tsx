import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { api } from "../src/api/client";
import type { ApprovalItem, Rule, UsageSummary } from "../src/api/types.gen";
import { ApprovalCard } from "../src/screens/chat/ApprovalCard";
import OnboardingScreen from "../src/screens/onboarding/OnboardingScreen";
import { RuleEditor } from "../src/screens/rules/RuleEditor";
import UsageScreen from "../src/screens/usage/UsageScreen";

const approval: ApprovalItem = { id: "approval-state", conversation_id: "conversation", title: "Create draft", action: "gmail.create_draft", status: "pending", created_at: "2026-09-30T12:00:00Z", payload: { body: "Hello" }, preview: "A draft only.", review_note: "This will not send mail.", review_verdict: "ok" };
const rule: Rule = { id: "locked", name: "Never send money", action: "payments.transfer", behavior: "handoff", created_at: "2026-09-30T12:00:00Z", locked: true, core_deny: true };
const usage: UsageSummary = { days: 7, total_credits: 1, today_credits: 1, plan_label: "ChatGPT Plus", manage_usage_url: "https://chatgpt.com/#settings/Usage", budgets: { task_credits: 1, daily_credits: 5, daily_hard_stop: true }, by_day: [], by_task: [], by_job_type: [] };

describe("M3 component coverage", () => {
  it("updates an approval card to a terminal state and removes decision controls", async () => {
    vi.spyOn(api, "call").mockResolvedValue({ approval: { ...approval, status: "denied" }, executed: false, result_summary: "Draft denied." });
    render(<MemoryRouter><ApprovalCard approval={approval} onDecision={vi.fn()} /></MemoryRouter>);
    fireEvent.click(screen.getByRole("button", { name: "Deny" }));
    expect(await screen.findByText("Draft denied.")).toBeVisible();
    expect(screen.getByText("denied")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Always allow this" })).not.toBeInTheDocument();
  });

  it("does not submit incomplete or locked rules", async () => {
    const call = vi.spyOn(api, "call");
    render(<RuleEditor onClose={vi.fn()} onSaved={async () => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Save rule" }));
    expect(call).not.toHaveBeenCalled();

    render(<RuleEditor rule={rule} onClose={vi.fn()} onSaved={async () => {}} />);
    fireEvent.click(screen.getAllByRole("button", { name: "Save rule" })[1]);
    expect(call).not.toHaveBeenCalled();
  });

  it("keeps invalid usage budgets in the form without sending them", async () => {
    const call = vi.spyOn(api, "call").mockResolvedValue(usage);
    render(<UsageScreen />);
    await screen.findByRole("button", { name: "Save budgets" });
    const task = screen.getByLabelText("Per-task budget (credits)") as HTMLInputElement;
    fireEvent.change(task, { target: { value: "-1" } });
    expect(task.validity.valid).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Save budgets" }));
    await waitFor(() => expect(call).toHaveBeenCalledWith("usage_get", { body: { days: 7 } }));
    expect(call).not.toHaveBeenCalledWith("usage_budgets_set", expect.anything());
  });

  it("stops onboarding for a signed-in Go account without acknowledging usage", async () => {
    const call = vi.spyOn(api, "call").mockResolvedValue({ current_step: "weekly_limit", completed_steps: ["companion", "chatgpt"], chatgpt: { state: "signed_in", plan: "ineligible", eligible: false, ineligible_reason: "Go accounts cannot share plan usage.", manage_usage_url: "https://chatgpt.com/#settings/Usage" } });
    render(<OnboardingScreen />);
    expect(await screen.findByRole("heading", { name: "ChatGPT Plus or Pro is required" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Acknowledge usage settings" })).not.toBeInTheDocument();
    expect(call).not.toHaveBeenCalledWith("onboarding_acknowledge", expect.anything());
  });
});
