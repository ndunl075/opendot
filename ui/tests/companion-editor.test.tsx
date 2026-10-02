import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { api } from "../src/api/client";
import type { CompanionProfile } from "../src/api/types.gen";
import { CompanionEditor } from "../src/screens/companion/CompanionEditor";

const profile: CompanionProfile = { name: "Moss", avatar_seed: "moss", created_at: "2026-09-30", paused: false, style_preset: "warm" };

it("previews name and seed locally, then saves only supported profile fields", async () => {
  const seed = "v2:c=jade;h=gumdrop;p=fox";
  const call = vi.spyOn(api, "call").mockImplementation(async endpoint => ({ ...profile, name: "Fern", avatar_seed: endpoint === "companion_avatar" ? seed : "moss" }));
  const close = vi.fn();
  render(<CompanionEditor companion={profile} onUpdated={vi.fn()} onClose={close} />);
  fireEvent.click(screen.getByRole("button", { name: "Rename companion" }));
  fireEvent.change(screen.getByLabelText("Companion name"), { target: { value: "Fern" } });
  fireEvent.keyDown(screen.getByLabelText("Companion name"), { key: "Enter" });
  fireEvent.click(screen.getByRole("radio", { name: "Color: Jade" }));
  fireEvent.click(screen.getByRole("radio", { name: "Character: Mint gumdrop" }));
  fireEvent.click(screen.getByRole("radio", { name: "Pet: Fox" }));
  const preview = screen.getByRole("region", { name: "Companion preview" });
  expect(within(preview).getByRole("heading", { name: "Fern" })).toBeVisible();
  expect(screen.getByRole("radio", { name: "Character: Mint gumdrop" })).toHaveAttribute("aria-checked", "true");
  expect(within(preview).getByRole("img").querySelector('[data-pet="fox"]')).toBeInTheDocument();
  expect(call).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(close).toHaveBeenCalledOnce());
  expect(call.mock.calls).toEqual([
    ["companion_rename", { body: { name: "Fern" } }],
    ["companion_avatar", { body: { avatar_seed: seed } }],
  ]);
});

it("cancel discards the draft without making a request", () => {
  const call = vi.spyOn(api, "call");
  const close = vi.fn();
  render(<CompanionEditor companion={profile} onUpdated={vi.fn()} onClose={close} />);
  fireEvent.click(screen.getByRole("radio", { name: "Color: Sky" }));
  fireEvent.click(screen.getByRole("button", { name: "Close dialog" }));
  expect(close).toHaveBeenCalledOnce();
  expect(call).not.toHaveBeenCalled();
});

it("keeps a failed save open and retries only the unsaved avatar after a saved rename", async () => {
  let attempts = 0;
  const call = vi.spyOn(api, "call").mockImplementation(async endpoint => {
    if (endpoint === "companion_avatar" && attempts++ === 0) throw new Error("Avatar could not be saved");
    return { ...profile, name: "Fern" };
  });
  const close = vi.fn();
  const updated = vi.fn();
  render(<CompanionEditor companion={profile} onUpdated={updated} onClose={close} />);
  fireEvent.click(screen.getByRole("button", { name: "Rename companion" }));
  fireEvent.change(screen.getByLabelText("Companion name"), { target: { value: "Fern" } });
  fireEvent.click(screen.getByRole("radio", { name: "Color: Sky" }));
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await screen.findByText("Avatar could not be saved");
  expect(close).not.toHaveBeenCalled();
  expect(updated).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(close).toHaveBeenCalledOnce());
  expect(call.mock.calls.filter(([endpoint]) => endpoint === "companion_rename")).toHaveLength(1);
  expect(call.mock.calls.filter(([endpoint]) => endpoint === "companion_avatar")).toHaveLength(2);
});

it("preserves a legacy ribbon when saving without changing choices", async () => {
  const call = vi.spyOn(api, "call");
  const close = vi.fn();
  render(<CompanionEditor companion={profile} onUpdated={vi.fn()} onClose={close} />);
  expect(screen.getByRole("img", { name: "Selected companion avatar" }).querySelectorAll("path")).toHaveLength(3);
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(close).toHaveBeenCalledOnce());
  expect(call).not.toHaveBeenCalled();
});

it("Escape cancels an inline rename without closing the editor", () => {
  const close = vi.fn();
  render(<CompanionEditor companion={profile} onUpdated={vi.fn()} onClose={close} />);
  fireEvent.click(screen.getByRole("button", { name: "Rename companion" }));
  fireEvent.change(screen.getByLabelText("Companion name"), { target: { value: "Fern" } });
  fireEvent.keyDown(screen.getByLabelText("Companion name"), { key: "Escape" });
  expect(screen.getByRole("heading", { name: "Moss" })).toBeVisible();
  expect(screen.getByRole("button", { name: "Rename companion" })).toHaveFocus();
  expect(close).not.toHaveBeenCalled();
});
