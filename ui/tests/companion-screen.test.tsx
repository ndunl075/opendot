import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { api, ApiError } from "../src/api/client";
import type { CompanionProfile } from "../src/api/types.gen";
import CompanionScreen from "../src/screens/companion/CompanionScreen";

const profile: CompanionProfile = { name: "Moss", avatar_seed: "moss", created_at: "2026-09-30T00:00:00Z", paused: false, style_preset: "warm" };

describe("Companion screen", () => {
  it("pauses then resumes through the companion endpoints", async () => {
    const call = vi.spyOn(api, "call").mockImplementation(async name => {
      if (name === "companion_tasks") return { in_progress: [], scheduled: [], completed: [] };
      return { ...profile, paused: name === "companion_pause" };
    });
    render(<CompanionScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Pause" }));
    expect(call).toHaveBeenCalledWith("companion_pause", { body: { reason: "Paused by you" } });
    fireEvent.click(await screen.findByRole("button", { name: "Resume" }));
    await waitFor(() => expect(call).toHaveBeenCalledWith("companion_resume"));
  });

  it("confirms reset and offers accessible task tabs", async () => {
    const call = vi.spyOn(api, "call").mockImplementation(async name => name === "companion_tasks" ? { in_progress: [], scheduled: [], completed: [] } : profile);
    render(<CompanionScreen />);
    fireEvent.click(await screen.findByRole("tab", { name: "Scheduled" }));
    expect(screen.getByText("Nothing scheduled")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Reset" }));
    expect(call).not.toHaveBeenCalledWith("companion_reset", expect.anything());
    fireEvent.click(within(screen.getByRole("dialog", { name: "Reset companion?" })).getByRole("button", { name: "Reset companion" }));
    await waitFor(() => expect(call).toHaveBeenCalledWith("companion_reset", { body: { confirm: "reset", forget_memory: true } }));
  });

  it("saves a new name and avatar to the daemon", async () => {
    const call = vi.spyOn(api, "call").mockImplementation(async name => name === "companion_tasks" ? { in_progress: [], scheduled: [], completed: [] } : profile);
    render(<CompanionScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Rename" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Companion name" }), { target: { value: "Fern" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(call).toHaveBeenCalledWith("companion_rename", { body: { name: "Fern" } }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Re-roll avatar" }));
    await waitFor(() => expect(call).toHaveBeenCalledWith("companion_avatar", { body: { avatar_seed: expect.any(String) } }));
  });

  it("shows an unavailable reset inside its confirmation dialog", async () => {
    vi.spyOn(api, "call").mockImplementation(async name => {
      if (name === "companion_reset") throw new ApiError(501, "not_implemented", "Not implemented");
      return name === "companion_tasks" ? { in_progress: [], scheduled: [], completed: [] } : profile;
    });
    render(<CompanionScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Reset" }));
    const dialog = screen.getByRole("dialog", { name: "Reset companion?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Reset companion" }));
    expect(await within(dialog).findByText("Not available in this version yet")).toBeVisible();
  });
});
