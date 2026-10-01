import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { api, ApiError } from "../src/api/client";
import type { ApprovalItem, PausedEvent } from "../src/api/types.gen";
import { ApprovalCard } from "../src/screens/chat/ApprovalCard";
import { PausedBanner } from "../src/screens/chat/PausedBanner";

const approval: ApprovalItem = { id: "approval-1", conversation_id: "c", title: "Draft a reply", action: "gmail_draft", status: "pending", created_at: "2026-09-30T12:00:00Z", payload: { body: "Hello" }, preview: "A draft only.", review_note: "This does not send email.", review_verdict: "ok" };
function card() { render(<MemoryRouter><ApprovalCard approval={approval} onDecision={vi.fn()} /></MemoryRouter>); }
describe("approval decisions", () => {
  it.each([["ok", "success"], ["concern", "warning"], ["block", "danger"]] as const)("shows the dedicated %s review separately from the preview", (verdict, tone) => {
    render(<MemoryRouter><ApprovalCard approval={{ ...approval, review_verdict: verdict, preview: "Reviewer (ok): literal preview text" }} onDecision={vi.fn()} /></MemoryRouter>);
    const review = screen.getByRole("region", { name: "Reviewer note" });
    expect(within(review).getByText(`Review: ${verdict}`)).toHaveClass(`badge--${tone}`);
    expect(review).toHaveTextContent("This does not send email.");
    expect(review).not.toHaveTextContent("literal preview text");
    expect(screen.getByLabelText("Action preview")).toHaveTextContent("Reviewer (ok): literal preview text");
  });
  it("does not infer a verdict or reviewer note from preview text", () => {
    render(<MemoryRouter><ApprovalCard approval={{ ...approval, review_note: null, review_verdict: null, preview: "Reviewer (block): literal preview text" }} onDecision={vi.fn()} /></MemoryRouter>);
    expect(screen.queryByRole("region", { name: "Reviewer note" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Action preview")).toHaveTextContent("Reviewer (block): literal preview text");
  });
  it.each([["Approve", "approval_approve", {}], ["Deny", "approval_deny", {}]] as const)("%s calls its endpoint once", async (label, endpoint, body) => {
    const call = vi.spyOn(api, "call").mockResolvedValue({ approval: { ...approval, status: "approved" }, executed: true });
    card(); expect(screen.getByText("This does not send email.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: label }));
    await waitFor(() => expect(call).toHaveBeenCalledWith(endpoint, { params: { approval_id: "approval-1" }, body }));
    expect(call).toHaveBeenCalledTimes(1);
  });
  it("edits only action payload and submits explicitly", async () => {
    const call = vi.spyOn(api, "call").mockResolvedValue({ approval, executed: false });
    card(); fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByLabelText("body"), { target: { value: "Changed" } });
    fireEvent.click(screen.getByRole("button", { name: "Save edit" }));
    await waitFor(() => expect(call).toHaveBeenCalledWith("approval_edit", { params: { approval_id: "approval-1" }, body: { payload: { body: "Changed" }, approve: false } }));
  });
  it("explains and confirms always allow, creating a rule", async () => {
    const call = vi.spyOn(api, "call").mockResolvedValue({ approval, executed: false, created_rule_id: "r1" });
    card(); fireEvent.click(screen.getByRole("button", { name: "Always allow this" }));
    expect(call).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Create rule" }));
    await waitFor(() => expect(call).toHaveBeenCalledWith("approval_always_allow", { params: { approval_id: "approval-1" }, body: { behavior: "auto_if_preapproved" } }));
    expect(await screen.findByRole("link", { name: "View created rule" })).toHaveAttribute("href", "/rules#r1");
  });
  it("leaves an unsuccessful decision actionable and shows 501 availability", async () => {
    vi.spyOn(api, "call").mockRejectedValue(new ApiError(501, "not_implemented", "Unavailable"));
    card(); fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(await screen.findByText("Not available in this version yet")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeEnabled();
  });
});

it.each<[PausedEvent["reason"], string]>([
  ["task_budget", "Task budget reached"], ["daily_budget", "Daily budget reached"], ["rate_limited", "ChatGPT plan limit reached"],
  ["top_tier_approval", "Top-tier approval needed"], ["anomaly", "Paused for unusual activity"], ["user", "Paused by you"],
])("explains paused reason %s with an action", (reason, title) => {
  render(<MemoryRouter><PausedBanner event={{ type: "paused", reason, message: "Daemon explanation", conversation_id: "c", message_id: "m", seq: 2 }} manageUsageUrl="https://chatgpt.com/#settings/Usage" /></MemoryRouter>);
  expect(screen.getByRole("alert")).toHaveTextContent(title);
  expect(screen.getByRole("alert")).toHaveTextContent("Daemon explanation");
  expect(screen.getAllByRole("link").length).toBeGreaterThan(0);
});
