import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { api, ApiError } from "../src/api/client";
import type { Settings, FeatureSwitchList, BackupStatus, BackupRestoreResult } from "../src/api/types.gen";
import { ThemeProvider } from "../src/design/theme";
import SettingsScreen from "../src/screens/settings/SettingsScreen";

const settings: Settings = {
  keep_awake: { enabled: false, only_while_plugged_in: true }, quiet_hours: { enabled: false, start: "22:00", end: "07:00", timezone: "UTC" },
  style_preset: "concise", tier_overrides: [], auto_top_tier: false,
  providers: [{ provider: "anthropic_key", label: "Anthropic API", enabled: false, configured: true, cost_warning: "API requests cost money." }],
};
const features: FeatureSwitchList = { features: [{ name: "claude_as_reviewer", title: "Use Claude as the reviewer", enabled: false, available: true, cost_warning: "Each review is billed to your API account.", requirement: "Anthropic key required", requires_any_of: ["anthropic_key"] }] };
const backups: BackupStatus = { backups: [{ id: "backup-1", created_at: "2026-09-30T12:00:00Z", size_bytes: 1200, verified: true }], encryption_key_present: true };
beforeEach(() => {
  vi.spyOn(api, "call").mockImplementation(async (name, options) => {
    if (name === "settings_get") return settings as never;
    if (name === "features_list") return features as never;
    if (name === "spend_get") return { providers: [] } as never;
    if (name === "backup_status") return backups as never;
    if (name === "backup_restore") return { backup_id: "backup-1", restored: true, restart_required: true } as never;
    if (name === "provider_api_key_save") return { provider: "anthropic_key", key_saved: true } as never;
    if (name === "provider_api_key_remove") return { provider: "anthropic_key", key_saved: false } as never;
    if (name === "feature_set") return { ...features.features[0], enabled: true } as never;
    if (name === "settings_update") return { ...settings, ...options?.body } as never;
    return settings as never;
  });
});

it("keeps saved API keys write-only and saving a key does not enable a provider", async () => {
  render(<ThemeProvider><SettingsScreen /></ThemeProvider>);
  const key = await screen.findByLabelText("Anthropic API API key");
  expect(key).toHaveAttribute("type", "password");
  expect(key).toHaveValue("");
  expect(screen.getByText("Key saved. Its value is never displayed. Saving a key does not enable this provider or its features.")).toBeInTheDocument();
  fireEvent.change(key, { target: { value: "test-user-entered-key" } });
  fireEvent.click(screen.getByRole("button", { name: "Save Anthropic API key" }));
  await waitFor(() => expect(api.call).toHaveBeenCalledWith("provider_api_key_save", { params: { provider: "anthropic_key" }, body: { api_key: "test-user-entered-key" } }));
  await waitFor(() => expect(screen.getByLabelText("Anthropic API API key")).toHaveValue(""));
  expect(api.call).not.toHaveBeenCalledWith("provider_enabled_set", expect.anything());
});

it("requires a cost acknowledgement before enabling a feature", async () => {
  render(<ThemeProvider><SettingsScreen /></ThemeProvider>);
  fireEvent.click(await screen.findByRole("switch", { name: "Use Claude as the reviewer" }));
  expect(api.call).not.toHaveBeenCalledWith("feature_set", expect.anything());
  const dialog = screen.getByRole("dialog", { name: "Enable Use Claude as the reviewer?" });
  expect(within(dialog).getByText("Each review is billed to your API account.")).toBeInTheDocument();
  fireEvent.click(within(dialog).getByRole("button", { name: "Enable feature" }));
  await waitFor(() => expect(api.call).toHaveBeenCalledWith("feature_set", { params: { feature: "claude_as_reviewer" }, body: { enabled: true } }));
});

it("shows version availability errors without fabricating settings", async () => {
  vi.mocked(api.call).mockRejectedValue(new ApiError(501, "not_implemented", "Unavailable"));
  render(<ThemeProvider><SettingsScreen /></ThemeProvider>);
  expect(await screen.findByText("Not available in this version yet")).toBeInTheDocument();
  expect(screen.queryByRole("switch", { name: "Keep awake" })).not.toBeInTheDocument();
});

it("keeps provider opt-in separate and waits for its cost confirmation", async () => {
  render(<ThemeProvider><SettingsScreen /></ThemeProvider>);
  fireEvent.click(await screen.findByRole("switch", { name: "Enable Anthropic API" }));
  expect(api.call).not.toHaveBeenCalledWith("provider_enabled_set", expect.anything());
  const dialog = screen.getByRole("dialog", { name: "Enable Anthropic API?" });
  expect(within(dialog).getByText(/API requests cost money/)).toBeInTheDocument();
  fireEvent.click(within(dialog).getByRole("button", { name: "Enable provider" }));
  await waitFor(() => expect(api.call).toHaveBeenCalledWith("provider_enabled_set", { params: { provider: "anthropic_key" }, body: { enabled: true } }));
  expect(api.call).not.toHaveBeenCalledWith("feature_set", expect.anything());
});

it("confirms removing a saved key before calling the destructive endpoint", async () => {
  render(<ThemeProvider><SettingsScreen /></ThemeProvider>);
  fireEvent.click(await screen.findByRole("button", { name: "Remove Anthropic API key" }));
  expect(api.call).not.toHaveBeenCalledWith("provider_api_key_remove", expect.anything());
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Remove key" }));
  await waitFor(() => expect(api.call).toHaveBeenCalledWith("provider_api_key_remove", { params: { provider: "anthropic_key" } }));
});

it("saves availability, quiet hours, style and job tier overrides through settings_update", async () => {
  render(<ThemeProvider><SettingsScreen /></ThemeProvider>);
  fireEvent.click(await screen.findByRole("switch", { name: "Keep awake" }));
  fireEvent.click(screen.getByRole("switch", { name: "Quiet hours" }));
  fireEvent.change(screen.getByLabelText("Quiet hours start"), { target: { value: "21:30" } });
  fireEvent.change(screen.getByLabelText("Quiet hours end"), { target: { value: "08:15" } });
  fireEvent.change(screen.getByLabelText("Quiet hours timezone"), { target: { value: "America/New_York" } });
  fireEvent.click(screen.getByRole("combobox", { name: "Style preset" }));
  fireEvent.click(screen.getByRole("option", { name: "Warm" }));
  fireEvent.click(screen.getByRole("button", { name: "Add model override" }));
  fireEvent.change(screen.getByLabelText("Job type 1"), { target: { value: "review" } });
  fireEvent.click(screen.getByRole("combobox", { name: "Model tier 1" }));
  fireEvent.click(screen.getByRole("option", { name: "Mid" }));
  fireEvent.click(screen.getByRole("combobox", { name: "Effort 1" }));
  fireEvent.click(screen.getByRole("option", { name: "Medium" }));
  fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
  await waitFor(() => expect(api.call).toHaveBeenCalledWith("settings_update", { body: {
    keep_awake: { enabled: true, only_while_plugged_in: true },
    quiet_hours: { enabled: true, start: "21:30", end: "08:15", timezone: "America/New_York" },
    style_preset: "warm", tier_overrides: [{ job_type: "review", tier: "terra", effort: "medium" }],
  } }));
  expect(await screen.findByText("Settings saved.")).toBeInTheDocument();
});

it("refreshes feature availability after an API key is saved", async () => {
  const original = vi.mocked(api.call).getMockImplementation()!;
  let saved = false;
  vi.mocked(api.call).mockImplementation(async (name, options) => {
    if (name === "provider_api_key_save") saved = true;
    if (name === "features_list") return { features: [{ ...features.features[0], available: saved }] } as never;
    if (name === "settings_get") return { ...settings, providers: [{ ...settings.providers[0], configured: saved }] } as never;
    return original(name, options);
  });
  render(<ThemeProvider><SettingsScreen /></ThemeProvider>);
  expect(await screen.findByRole("switch", { name: "Use Claude as the reviewer" })).toBeDisabled();
  const timezone = screen.getByLabelText("Quiet hours timezone");
  fireEvent.change(timezone, { target: { value: "America/New_York" } });
  fireEvent.change(screen.getByLabelText("Anthropic API API key"), { target: { value: "test-user-entered-key" } });
  fireEvent.click(screen.getByRole("button", { name: "Save Anthropic API key" }));
  await waitFor(() => expect(screen.getByRole("switch", { name: "Use Claude as the reviewer" })).toBeEnabled());
  expect(screen.getByLabelText("Anthropic API API key")).toHaveValue("");
  expect(screen.getByText(/^Key saved\./)).toBeInTheDocument();
  expect(screen.getByLabelText("Quiet hours timezone")).toBe(timezone);
  expect(timezone).toHaveValue("America/New_York");
  expect(api.call).not.toHaveBeenCalledWith("feature_set", expect.anything());
});

it("restores only after confirmation and keeps the dialog open while the daemon responds", async () => {
  const original = vi.mocked(api.call).getMockImplementation()!;
  let finish!: (result: BackupRestoreResult) => void;
  const pending = new Promise<BackupRestoreResult>(resolve => { finish = resolve; });
  vi.mocked(api.call).mockImplementation(async (name, options) => {
    if (name === "backup_restore") return await pending as never;
    return original(name, options);
  });
  render(<ThemeProvider><SettingsScreen /></ThemeProvider>);
  fireEvent.click(await screen.findByRole("button", { name: "Restore backup backup-1" }));
  expect(api.call).not.toHaveBeenCalledWith("backup_restore", expect.anything());
  const dialog = screen.getByRole("dialog", { name: "Restore this backup?" });
  fireEvent.click(within(dialog).getByRole("button", { name: "Restore backup" }));
  await waitFor(() => expect(api.call).toHaveBeenCalledWith("backup_restore", { body: { backup_id: "backup-1", confirm: "restore" } }));
  fireEvent.keyDown(dialog, { key: "Escape" });
  expect(screen.getByRole("dialog", { name: "Restore this backup?" })).toBeInTheDocument();
  await act(async () => { finish({ backup_id: "backup-1", restored: true, restart_required: true }); });
  expect(await screen.findByText("Backup restored. Restart the daemon to finish restoring.")).toBeInTheDocument();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("shows a restore 501 error inside the still-open confirmation dialog", async () => {
  const original = vi.mocked(api.call).getMockImplementation()!;
  vi.mocked(api.call).mockImplementation(async (name, options) => {
    if (name === "backup_restore") throw new ApiError(501, "not_implemented", "Unavailable");
    return original(name, options);
  });
  render(<ThemeProvider><SettingsScreen /></ThemeProvider>);
  fireEvent.click(await screen.findByRole("button", { name: "Restore backup backup-1" }));
  const dialog = screen.getByRole("dialog", { name: "Restore this backup?" });
  fireEvent.click(within(dialog).getByRole("button", { name: "Restore backup" }));
  expect(await within(dialog).findByText("Not available in this version yet")).toBeInTheDocument();
  expect(screen.queryByText(/Backup restored/)).not.toBeInTheDocument();
});
