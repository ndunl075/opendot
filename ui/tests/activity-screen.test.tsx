import { MemoryRouter } from "react-router-dom";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { api, ApiError } from "../src/api/client";
import ActivityScreen from "../src/screens/activity";

describe("Activity screen", () => {
  it.each([["ok", "success"], ["concern", "warning"], ["block", "danger"]] as const)("loads dedicated approval review fields for %s", async (verdict, tone) => {
    const call = vi.spyOn(api, "call").mockResolvedValueOnce({ items: [{ id: "event", at: "2026-09-30", kind: "approval", title: "Reviewed draft", why: "Requested", approval_id: "approval/a", reviewer_note: "Old activity note" }] }).mockResolvedValue({ id: "approval/a", title: "Draft", action: "gmail_draft", status: "pending", created_at: "2026-09-30", payload: {}, preview: "Reviewer (ok): preview only", review_note: "Dedicated durable note", review_verdict: verdict });
    render(<MemoryRouter initialEntries={[window.location.pathname + window.location.hash]}><ActivityScreen /></MemoryRouter>);
    expect(await screen.findByText("Dedicated durable note")).toBeInTheDocument();
    expect(screen.getByText(`Review: ${verdict}`)).toHaveClass(`badge--${tone}`);
    expect(screen.queryByText("Old activity note")).not.toBeInTheDocument();
    expect(screen.queryByText(/preview only/)).not.toBeInTheDocument();
    expect(call).toHaveBeenCalledWith("approval_get", { params: { approval_id: "approval/a" } });
  });
  it("keeps the activity visible if its approval cannot be loaded", async () => {
    vi.spyOn(api, "call").mockResolvedValueOnce({ items: [{ id: "event", at: "2026-09-30", kind: "approval", title: "Reviewed draft", why: "Requested", approval_id: "missing" }] }).mockRejectedValue(new Error("Not found"));
    render(<MemoryRouter initialEntries={[window.location.pathname + window.location.hash]}><ActivityScreen /></MemoryRouter>);
    expect(await screen.findByText("Something went wrong")).toBeInTheDocument();
    expect(screen.getByText("Reviewed draft")).toBeInTheDocument();
  });
  it("shows why, links evidence, and loads older events", async () => {
    const call = vi.spyOn(api, "call").mockResolvedValueOnce({ items: [{ id: "event-1", at: "2026-09-30T12:00:00Z", kind: "action", title: "Prepared a draft", why: "You asked for a reply", rule_id: "rule-1", rule_name: "Review drafts", reviewer_note: "Recipient checked", memory_ids: ["memory-1"] }], next_cursor: "older" }).mockResolvedValue({ items: [], next_cursor: null });
    render(<MemoryRouter initialEntries={[window.location.pathname + window.location.hash]}><ActivityScreen /></MemoryRouter>);
    expect(await screen.findByText("You asked for a reply")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Rule: Review drafts" })).toHaveAttribute("href", "/rules#rule-1");
    expect(screen.getByRole("link", { name: "Memory memory-1" })).toHaveAttribute("href", "/memory#memory-1");
    fireEvent.click(screen.getByText("Reviewer note"));
    expect(screen.getByText("Recipient checked")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Load older activity" }));
    await screen.findByText("Prepared a draft");
    expect(call).toHaveBeenCalledWith("activity_list", { body: { limit: 50, cursor: "older" } });
  });
  it("explains unsupported activity", async () => {
    vi.spyOn(api, "call").mockRejectedValue(new ApiError(501, "not_implemented", "Unavailable"));
    render(<MemoryRouter initialEntries={[window.location.pathname + window.location.hash]}><ActivityScreen /></MemoryRouter>);
    expect(await screen.findByText("Not available in this version yet")).toBeInTheDocument();
  });
});
