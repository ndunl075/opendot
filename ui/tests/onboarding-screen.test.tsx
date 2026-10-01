import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { api, ApiError } from "../src/api/client";
import type { OnboardingState } from "../src/api/types.gen";
import OnboardingScreen from "../src/screens/onboarding/OnboardingScreen";

const initial: OnboardingState = { current_step: "chatgpt", completed_steps: ["companion"], companion_name: "Moss", avatar_seed: "moss", chatgpt: { state: "signed_out", eligible: false, plan: "unknown", manage_usage_url: "https://chatgpt.com/#settings/Usage" } };

describe("Onboarding screen", () => {
  it("blocks credit acknowledgement while credits are on and rechecks status", async () => {
    const ready: OnboardingState = { ...initial, current_step: "weekly_limit", chatgpt: { ...initial.chatgpt, state: "signed_in", eligible: true, plan: "eligible_plus", credits_enabled: true } };
    const call = vi.spyOn(api, "call").mockImplementation(async name => (name === "chatgpt_status" ? { ...ready.chatgpt, credits_enabled: false } : ready) as never);
    render(<OnboardingScreen />);
    const credits = await screen.findByRole("checkbox", { name: "I kept ChatGPT credit use off" });
    expect(credits).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: "I set a weekly OpenDot limit" }));
    expect(screen.getByRole("button", { name: "Acknowledge usage settings" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Check again" }));
    await waitFor(() => expect(credits).toBeEnabled()); expect(credits).not.toBeChecked();
    expect(call).toHaveBeenCalledWith("chatgpt_status");
    fireEvent.click(credits); expect(screen.getByRole("button", { name: "Acknowledge usage settings" })).toBeEnabled();
    expect(call).not.toHaveBeenCalledWith("onboarding_acknowledge", expect.anything());
  });
  it("saves the chosen name and avatar through the onboarding contract", async () => {
    const call = vi.spyOn(api, "call").mockResolvedValue({ ...initial, current_step: "companion" });
    render(<OnboardingScreen />);
    fireEvent.change(await screen.findByRole("textbox", { name: "Companion name" }), { target: { value: "Cedar" } });
    fireEvent.click(screen.getByRole("button", { name: "Choose avatar 2" }));
    fireEvent.click(screen.getByRole("button", { name: "Save companion" }));
    await waitFor(() => expect(call).toHaveBeenCalledWith("onboarding_companion", { body: { name: "Cedar", avatar_seed: "open-fold-2" } }));
  });

  it("polls sign-in and stops an account without Plus or Pro", async () => {
    const call = vi.spyOn(api, "call").mockImplementation(async name => {
      if (name === "onboarding_get") return initial;
      if (name === "chatgpt_start") return { state: "pending", authorize_url: "https://example.test/auth", expires_at: "2099-01-01T00:00:00Z", poll_interval_seconds: 1 };
      if (name === "chatgpt_status") return { ...initial.chatgpt, state: "signed_in", plan: "ineligible", ineligible_reason: "Free accounts cannot share plan usage." };
      throw new Error(`Unexpected ${name}`);
    });
    render(<OnboardingScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Continue with ChatGPT" }));
    expect(await screen.findByText(/ChatGPT Plus or Pro is required/)).toBeVisible();
    expect(screen.queryByRole("button", { name: "Acknowledge usage settings" })).not.toBeInTheDocument();
    expect(call).toHaveBeenCalledWith("chatgpt_start", { body: { open_browser: true } });
    expect(call).toHaveBeenCalledWith("chatgpt_status");
    expect(call).not.toHaveBeenCalledWith("onboarding_complete");
  });

  it("requires both usage acknowledgements before advancing", async () => {
    const ready: OnboardingState = { ...initial, current_step: "weekly_limit", chatgpt: { ...initial.chatgpt, state: "signed_in", eligible: true, plan: "eligible_plus" } };
    const call = vi.spyOn(api, "call").mockResolvedValue(ready);
    render(<OnboardingScreen />);
    const button = await screen.findByRole("button", { name: "Acknowledge usage settings" });
    expect(button).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: "I set a weekly OpenDot limit" }));
    expect(button).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: "I kept ChatGPT credit use off" }));
    fireEvent.click(button);
    await waitFor(() => expect(call).toHaveBeenCalledWith("onboarding_acknowledge", { body: { weekly_limit_set: true, credits_off: true } }));
  });

  it("requires an explicit credit-use acknowledgement when ChatGPT cannot report the setting", async () => {
    const ready: OnboardingState = { ...initial, current_step: "weekly_limit", chatgpt: { ...initial.chatgpt, state: "signed_in", eligible: true, plan: "eligible_plus", credits_enabled: null } };
    const call = vi.spyOn(api, "call").mockResolvedValue(ready);
    render(<OnboardingScreen />);
    expect(await screen.findByText("OpenDot can't check this setting. In ChatGPT Settings, Usage, make sure credit use is off.")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Check again" })).not.toBeInTheDocument();
    const acknowledgement = screen.getByRole("button", { name: "Acknowledge usage settings" });
    fireEvent.click(screen.getByRole("checkbox", { name: "I set a weekly OpenDot limit" }));
    expect(acknowledgement).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: "I kept ChatGPT credit use off" }));
    fireEvent.click(acknowledgement);
    await waitFor(() => expect(call).toHaveBeenCalledWith("onboarding_acknowledge", { body: { weekly_limit_set: true, credits_off: true } }));
  });

  it("explains an unavailable onboarding endpoint", async () => {
    vi.spyOn(api, "call").mockRejectedValue(new ApiError(501, "not_implemented", "Unavailable"));
    render(<OnboardingScreen />);
    expect(await screen.findByText("Not available in this version yet")).toBeVisible();
  });

  it("introduces the companion before opening chat after confirmed completion", async () => {
    const onComplete = vi.fn();
    vi.spyOn(api, "call").mockImplementation(async name => name === "onboarding_get" ? { ...initial, current_step: "connections", chatgpt: { ...initial.chatgpt, state: "signed_in", eligible: true, plan: "eligible_plus" } } : { ...initial, current_step: "done", completed_steps: ["done"], intro_message: "Hello from Moss.", chatgpt: { ...initial.chatgpt, state: "signed_in", eligible: true, plan: "eligible_plus" } });
    render(<OnboardingScreen onComplete={onComplete} />);
    fireEvent.click(await screen.findByRole("button", { name: "Continue to introduction" }));
    expect(await screen.findByText("Hello from Moss.")).toBeVisible();
    expect(onComplete).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Open chat" }));
    expect(onComplete).toHaveBeenCalledOnce();
  });
});
