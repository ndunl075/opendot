import { MemoryRouter } from "react-router-dom";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { api, ApiError } from "../src/api/client";
import type { MemorySearchResult } from "../src/api/types.gen";
import MemoryScreen from "../src/screens/memory";

const results: MemorySearchResult = { total: 1, items: [{ id: "memory-1", statement: "Prefers morning meetings", confidence: "confirmed", source: "gmail", source_label: "Gmail", learned_at: "2026-09-30T12:00:00Z", people: ["Alex"] }] };
describe("Memory screen", () => {
  it("searches and corrects using contract requests", async () => {
    const call = vi.spyOn(api, "call").mockResolvedValue(results);
    render(<MemoryRouter initialEntries={[window.location.pathname + window.location.hash]}><MemoryScreen /></MemoryRouter>);
    await screen.findByText("Prefers morning meetings");
    fireEvent.change(screen.getByLabelText("Search memories"), { target: { value: "meetings" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    await screen.findByText("Prefers morning meetings");
    expect(call).toHaveBeenCalledWith("memory_search", { body: { q: "meetings", limit: 100 } });
    fireEvent.click(screen.getByRole("button", { name: "Correct memory" }));
    fireEvent.change(screen.getByLabelText("Corrected memory"), { target: { value: "Prefers afternoon meetings" } });
    call.mockResolvedValueOnce({ corrected: { ...results.items[0], statement: "Prefers afternoon meetings" }, superseded_id: "memory-1" });
    fireEvent.click(screen.getByRole("button", { name: "Save correction" }));
    await screen.findByText("Memory corrected.");
    expect(call).toHaveBeenCalledWith("memory_correct", { params: { memory_id: "memory-1" }, body: { statement: "Prefers afternoon meetings" } });
  });
  it("requires confirmation before forgetting a source", async () => {
    const call = vi.spyOn(api, "call").mockResolvedValue(results);
    render(<MemoryRouter initialEntries={[window.location.pathname + window.location.hash]}><MemoryScreen /></MemoryRouter>);
    await screen.findByText("Prefers morning meetings");
    fireEvent.click(screen.getByRole("button", { name: "Forget memories" }));
    fireEvent.change(screen.getByLabelText("Forget by"), { target: { value: "source" } });
    fireEvent.change(screen.getByLabelText("Source to forget"), { target: { value: "gmail" } });
    fireEvent.click(screen.getByRole("button", { name: "Review forget request" }));
    expect(call).not.toHaveBeenCalledWith("memory_forget", expect.anything());
    call.mockResolvedValueOnce({ forgotten_count: 1, scope: "source" });
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Confirm forget" }));
    await screen.findByText("Forgot 1 memory.");
    expect(call).toHaveBeenCalledWith("memory_forget", { body: { scope: "source", source: "gmail" } });
  });
  it("confirms forgetting an individual memory and focuses Cancel", async () => {
    const call = vi.spyOn(api, "call").mockResolvedValue(results);
    render(<MemoryRouter initialEntries={[window.location.pathname + window.location.hash]}><MemoryScreen /></MemoryRouter>);
    fireEvent.click(await screen.findByRole("button", { name: "Forget memory" }));
    expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus();
    call.mockResolvedValueOnce({ forgotten_count: 1, scope: "item" });
    fireEvent.click(screen.getByRole("button", { name: "Confirm forget" }));
    await screen.findByText("Forgot 1 memory.");
    expect(call).toHaveBeenCalledWith("memory_forget", { body: { scope: "item", item_id: "memory-1" } });
  });
  it("forgets by person only after review", async () => {
    const call = vi.spyOn(api, "call").mockResolvedValue(results);
    render(<MemoryRouter initialEntries={[window.location.pathname + window.location.hash]}><MemoryScreen /></MemoryRouter>);
    await screen.findByText("Prefers morning meetings");
    fireEvent.click(screen.getByRole("button", { name: "Forget memories" }));
    fireEvent.change(screen.getByLabelText("Forget by"), { target: { value: "person" } });
    fireEvent.change(screen.getByLabelText("Person to forget"), { target: { value: "Alex" } });
    fireEvent.click(screen.getByRole("button", { name: "Review forget request" }));
    expect(screen.getByText(/all memories about “Alex”/)).toBeInTheDocument();
    call.mockResolvedValueOnce({ forgotten_count: 1, scope: "person" });
    fireEvent.click(screen.getByRole("button", { name: "Confirm forget" }));
    await screen.findByText("Forgot 1 memory.");
    expect(call).toHaveBeenCalledWith("memory_forget", { body: { scope: "person", person: "Alex" } });
  });
  it("sends a reviewed time range as ISO timestamps", async () => {
    const call = vi.spyOn(api, "call").mockResolvedValue(results);
    render(<MemoryRouter initialEntries={[window.location.pathname + window.location.hash]}><MemoryScreen /></MemoryRouter>);
    await screen.findByText("Prefers morning meetings");
    fireEvent.click(screen.getByRole("button", { name: "Forget memories" }));
    fireEvent.change(screen.getByLabelText("Forget by"), { target: { value: "time" } });
    fireEvent.change(screen.getByLabelText("Forget from"), { target: { value: "2026-09-01T09:00" } });
    fireEvent.change(screen.getByLabelText("Forget until"), { target: { value: "2026-09-30T09:00" } });
    fireEvent.click(screen.getByRole("button", { name: "Review forget request" }));
    call.mockResolvedValueOnce({ forgotten_count: 1, scope: "time" });
    fireEvent.click(screen.getByRole("button", { name: "Confirm forget" }));
    await screen.findByText("Forgot 1 memory.");
    expect(call).toHaveBeenCalledWith("memory_forget", { body: { scope: "time", since: new Date("2026-09-01T09:00").toISOString(), until: new Date("2026-09-30T09:00").toISOString() } });
  });
  it("explains unsupported memory", async () => {
    vi.spyOn(api, "call").mockRejectedValue(new ApiError(501, "not_implemented", "Unavailable"));
    render(<MemoryRouter initialEntries={[window.location.pathname + window.location.hash]}><MemoryScreen /></MemoryRouter>);
    expect(await screen.findByText("Not available in this version yet")).toBeInTheDocument();
  });
});
