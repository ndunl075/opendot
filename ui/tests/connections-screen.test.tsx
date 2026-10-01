import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { api } from "../src/api/client";
import type { Connection } from "../src/api/types.gen";
import ConnectionsScreen from "../src/screens/connections/ConnectionsScreen";

const gmail: Connection = { id: "gmail-1", app: "gmail", account_label: "demo@example.test", health: "stale", read_only: true, write_opt_in: false, write_opt_in_available: true };

describe("Connections screen", () => {
  it("requires confirmation to grant write access", async () => {
    const call = vi.spyOn(api, "call").mockImplementation(async name => name === "connections_list" ? { connections: [gmail] } : { ...gmail, write_opt_in: true, read_only: false });
    render(<ConnectionsScreen />);
    fireEvent.click(await screen.findByRole("switch", { name: "Allow Gmail drafts" }));
    expect(call).not.toHaveBeenCalledWith("connection_write_opt_in", expect.anything());
    fireEvent.click(within(screen.getByRole("dialog", { name: "Allow Gmail drafts?" })).getByRole("button", { name: "Allow write access" }));
    await waitFor(() => expect(call).toHaveBeenCalledWith("connection_write_opt_in", { params: { connection_id: "gmail-1" }, body: { enabled: true } }));
  });

  it("offers forgetting learned memories when disconnecting", async () => {
    const call = vi.spyOn(api, "call").mockImplementation(async name => name === "connections_list" ? { connections: [gmail] } : { disconnected: true, forgotten_count: 2 });
    render(<ConnectionsScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Disconnect Gmail" }));
    const dialog = screen.getByRole("dialog", { name: "Disconnect Gmail?" });
    fireEvent.click(within(dialog).getByRole("checkbox", { name: "Forget everything learned from this account" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Disconnect account" }));
    await waitFor(() => expect(call).toHaveBeenCalledWith("connection_disconnect", { params: { connection_id: "gmail-1" }, body: { forget_learned: true } }));
  });

  it("connects read-only and renders all health states", async () => {
    const call = vi.spyOn(api, "call").mockImplementation(async name => name === "connections_list" ? { connections: (['ok', 'stale', 'error', 'never_synced'] as const).map((health, i) => ({ ...gmail, id: `account-${i}`, health })) } : { app: "github", state: "pending", authorize_url: "https://example.test/github" });
    render(<ConnectionsScreen />);
    expect(await screen.findByText("Never synced")).toBeVisible();
    for (const label of ["OK", "Stale", "Error"]) expect(screen.getByText(label)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Connect GitHub" }));
    expect(await screen.findByRole("link", { name: "Continue to GitHub authorization" })).toHaveAttribute("href", "https://example.test/github");
    expect(call).toHaveBeenCalledWith("connection_start", { body: { app: "github" } });
  });
});
