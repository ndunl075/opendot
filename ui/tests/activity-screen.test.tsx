import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { api, ApiError } from "../src/api/client";
import ActivityScreen from "../src/screens/activity";

describe("Activity screen", () => {
  it("shows why, links evidence, and loads older events", async () => {
    const call = vi.spyOn(api, "call").mockResolvedValueOnce({ items: [{ id: "event-1", at: "2026-09-30T12:00:00Z", kind: "action", title: "Prepared a draft", why: "You asked for a reply", rule_id: "rule-1", rule_name: "Review drafts", reviewer_note: "Recipient checked", memory_ids: ["memory-1"] }], next_cursor: "older" }).mockResolvedValue({ items: [], next_cursor: null });
    render(<ActivityScreen />);
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
    render(<ActivityScreen />);
    expect(await screen.findByText("Not available in this version yet")).toBeInTheDocument();
  });
});
