import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { api, ApiError } from "../src/api/client";
import type { Rule } from "../src/api/types.gen";
import RulesScreen from "../src/screens/rules";

const rule: Rule = { id: "read", name: "Read mail", action: "gmail.read", behavior: "auto", created_at: "2026-09-30T12:00:00Z" };
describe("Rules screen", () => {
  it("locks core deny rules even without the locked flag", async () => {
    vi.spyOn(api, "call").mockResolvedValue({ rules: [{ ...rule, id: "deny", name: "Spending money", core_deny: true, behavior: "handoff" }] });
    render(<RulesScreen />);
    const card = await screen.findByRole("article", { name: "Spending money" });
    expect(within(card).getByText(/Locked/)).toBeInTheDocument();
    expect(within(card).queryByRole("button")).not.toBeInTheDocument();
  });
  it("shows core refusal even when the response reports an ask behavior", async () => {
    vi.spyOn(api, "call").mockResolvedValue({ rules: [{ ...rule, id: "deny", name: "Spending money", action: "payments.transfer", core_deny: true, behavior: "ask" }] });
    render(<RulesScreen />);
    const card = await screen.findByRole("article", { name: "Spending money" });
    expect(within(card).getByText(/Locked/)).toBeInTheDocument();
    expect(within(card).getByText("Hand off to me")).toBeInTheDocument();
    expect(within(card).getByText(/This action is prohibited for the companion. No rule or approval can permit it./)).toBeInTheDocument();
    expect(within(card).queryByText("Ask first")).not.toBeInTheDocument();
    expect(within(card).queryByRole("button")).not.toBeInTheDocument();
  });
  it("creates typed rules and confirms deletion", async () => {
    const call = vi.spyOn(api, "call").mockResolvedValue({ rules: [rule] });
    render(<RulesScreen />);
    await screen.findByRole("article", { name: "Read mail" });
    fireEvent.click(screen.getByRole("button", { name: "Create rule" }));
    fireEvent.change(screen.getByLabelText("Rule name"), { target: { value: "Draft review" } });
    fireEvent.change(screen.getByLabelText("Action"), { target: { value: "gmail.draft" } });
    call.mockResolvedValueOnce({ ...rule, name: "Draft review" });
    fireEvent.click(screen.getByRole("button", { name: "Save rule" }));
    await screen.findByText("Rule created.");
    expect(call).toHaveBeenCalledWith("rule_create", { body: { name: "Draft review", action: "gmail.draft", behavior: "ask", description: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Delete Read mail" }));
    expect(call).not.toHaveBeenCalledWith("rule_delete", expect.anything());
    call.mockResolvedValueOnce({ ok: true });
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete rule" }));
    await screen.findByText("Rule deleted.");
    expect(call).toHaveBeenCalledWith("rule_delete", { params: { rule_id: "read" } });
  });
  it("explains unavailable endpoints", async () => {
    vi.spyOn(api, "call").mockRejectedValue(new ApiError(501, "not_implemented", "Unavailable"));
    render(<RulesScreen />);
    expect(await screen.findByText("Not available in this version yet")).toBeInTheDocument();
  });
  it("edits behavior without changing an existing action", async () => {
    const call = vi.spyOn(api, "call").mockResolvedValue({ rules: [rule] });
    render(<RulesScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit Read mail" }));
    expect(screen.getByLabelText("Action")).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Behavior"), { target: { value: "auto_if_preapproved" } });
    expect(screen.getByText(/explicitly asked for this exact action/)).toBeInTheDocument();
    call.mockResolvedValueOnce({ ...rule, behavior: "auto_if_preapproved" });
    fireEvent.click(screen.getByRole("button", { name: "Save rule" }));
    await screen.findByText("Rule updated.");
    expect(call).toHaveBeenCalledWith("rule_update", { params: { rule_id: "read" }, body: { name: "Read mail", description: "", behavior: "auto_if_preapproved", enabled: true } });
  });
});
