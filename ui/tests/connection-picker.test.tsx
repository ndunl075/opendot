import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "../src/api/client";
import { ConnectionPicker } from "../src/screens/connections/ConnectionPicker";

const setup = { configured: false, setup_steps: ["Create a Google project.", "Create a Desktop app OAuth client."] };
const authorization = { app: "gmail" as const, state: "pending", authorize_url: "https://example.test/google" };
const healthy = { id: "gmail-1", app: "gmail" as const, health: "ok" as const, account_label: "demo@example.test", read_only: true };
afterEach(() => vi.useRealTimers());

describe("Shared connection setup", () => {
  it("shows numbered daemon steps and clears the masked, write-only client secret after saving", async () => {
    const call = vi.spyOn(api, "call").mockImplementation(async name => name === "google_client_get" ? setup : name === "google_client_set" ? { configured: true } : { connections: [] });
    render(<ConnectionPicker />);
    expect(await screen.findByText(setup.setup_steps[0])).toBeVisible();
    expect(screen.getByText(setup.setup_steps[0]).closest("ol")).not.toBeNull();
    const secret = screen.getByLabelText("Google client secret");
    expect(secret).toHaveAttribute("type", "password");
    fireEvent.change(screen.getByLabelText("Google client ID"), { target: { value: "demo.apps.googleusercontent.com" } });
    fireEvent.change(secret, { target: { value: "synthetic-client-secret" } });
    fireEvent.click(screen.getByRole("button", { name: "Save Google client" }));
    await waitFor(() => expect(screen.queryByLabelText("Google client secret")).not.toBeInTheDocument());
    expect(call).toHaveBeenCalledWith("google_client_set", { body: { client_id: "demo.apps.googleusercontent.com", client_secret: "synthetic-client-secret" } });
    expect(document.body.innerHTML).not.toContain("synthetic-client-secret");
    expect(screen.getByRole("button", { name: "Connect Gmail" })).toBeEnabled();
  });

  it("opens Google externally and polls until healthy", async () => {
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    const refresh = vi.fn();
    let connected = false;
    vi.spyOn(api, "call").mockImplementation(async name => {
      if (name === "google_client_get") return { configured: true };
      if (name === "connection_start") return authorization;
      return { connections: connected ? [healthy] : [] };
    });
    render(<ConnectionPicker onRefresh={refresh} />);
    await screen.findByRole("button", { name: "Connect Gmail" });
    vi.useFakeTimers();
    fireEvent.click(screen.getByRole("button", { name: "Connect Gmail" }));
    await act(async () => {});
    expect(screen.getByText(/Waiting for Google/)).toBeVisible();
    expect(open).toHaveBeenCalledWith(authorization.authorize_url, "_blank", "noopener,noreferrer");
    expect(screen.getByRole("link", { name: "Open Google authorization" })).toHaveAttribute("rel", "noopener noreferrer");
    connected = true;
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(screen.getByText("Gmail is connected and healthy.")).toBeVisible();
    expect(refresh).toHaveBeenCalled();
  });

  it("routes a missing Google client back to setup", async () => {
    let missing = false;
    vi.spyOn(api, "call").mockImplementation(async name => {
      if (name === "google_client_get") return missing ? setup : { configured: true };
      if (name === "connection_start") { missing = true; throw new ApiError(409, "google_client_missing", "Set up your Google client first."); }
      return { connections: [] };
    });
    render(<ConnectionPicker />);
    fireEvent.click(await screen.findByRole("button", { name: "Connect Gmail" }));
    expect(await screen.findByLabelText("Google client secret")).toBeVisible();
    expect(screen.getByText("Set up your Google client first.")).toBeVisible();
  });

  it("saves and removes a masked GitHub token without redisplaying it", async () => {
    const call = vi.spyOn(api, "call").mockImplementation(async name => name === "google_client_get" ? setup : name === "github_token_set" ? { configured: true } : name === "github_token_remove" ? { configured: false } : { connections: [] });
    render(<ConnectionPicker />);
    const token = await screen.findByLabelText("GitHub personal access token");
    expect(token).toHaveAttribute("type", "password");
    fireEvent.change(token, { target: { value: "synthetic-github-token" } });
    fireEvent.click(screen.getByRole("button", { name: "Save GitHub token" }));
    expect(await screen.findByText("GitHub token saved.")).toBeVisible();
    expect(token).toHaveValue("");
    expect(document.body.innerHTML).not.toContain("synthetic-github-token");
    expect(call).toHaveBeenCalledWith("github_token_set", { body: { token: "synthetic-github-token" } });
    fireEvent.click(screen.getByRole("button", { name: "Remove GitHub token" }));
    expect(await screen.findByText("GitHub token removed.")).toBeVisible();
    expect(call).toHaveBeenCalledWith("github_token_remove");
    expect(call).not.toHaveBeenCalledWith("connection_start", { body: { app: "github" } });
  });

  it.each(["Google", "GitHub"])("shows the server's invalid %s credential message", async app => {
    vi.spyOn(api, "call").mockImplementation(async name => {
      if (name === "google_client_get") return setup;
      if (name === "connections_list") return { connections: [] };
      throw new ApiError(422, app === "Google" ? "invalid_google_client" : "invalid_github_token", `Invalid ${app} credential. Check its format.`);
    });
    render(<ConnectionPicker />);
    await screen.findByLabelText("Google client ID");
    if (app === "Google") {
      fireEvent.change(screen.getByLabelText("Google client ID"), { target: { value: "invalid" } });
      fireEvent.change(screen.getByLabelText("Google client secret"), { target: { value: "synthetic-secret" } });
    } else fireEvent.change(screen.getByLabelText("GitHub personal access token"), { target: { value: "synthetic-token" } });
    fireEvent.click(screen.getByRole("button", { name: app === "Google" ? "Save Google client" : "Save GitHub token" }));
    expect(await screen.findByText(`Invalid ${app} credential. Check its format.`)).toBeVisible();
  });

  it("bounds polling, offers retry, and stops polling after cancellation", async () => {
    vi.spyOn(window, "open").mockReturnValue(null);
    const call = vi.spyOn(api, "call").mockImplementation(async name => name === "google_client_get" ? { configured: true } : name === "connection_start" ? authorization : { connections: [] });
    render(<ConnectionPicker />);
    await screen.findByRole("button", { name: "Connect Gmail" });
    vi.useFakeTimers();
    fireEvent.click(screen.getByRole("button", { name: "Connect Gmail" }));
    await act(async () => {});
    await act(async () => { await vi.advanceTimersByTimeAsync(120_000); });
    expect(screen.getByText(/Google has not confirmed access yet/)).toBeVisible();
    const count = call.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(call.mock.calls.length).toBe(count);
    fireEvent.click(screen.getByRole("button", { name: "Retry Google connection" }));
    await act(async () => {});
    fireEvent.click(screen.getByRole("button", { name: "Cancel waiting" }));
    const cancelledCount = call.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(call.mock.calls.length).toBe(cancelledCount);
    expect(screen.getByText(/Stopped waiting/)).toBeVisible();
  });

  it("ignores an in-flight poll after cancellation and unmount", async () => {
    vi.spyOn(window, "open").mockReturnValue(null);
    const refresh = vi.fn();
    let resolve!: (value: { connections: typeof healthy[] }) => void;
    const call = vi.spyOn(api, "call").mockImplementation(async name => {
      if (name === "google_client_get") return { configured: true };
      if (name === "connection_start") return authorization;
      return new Promise<{ connections: typeof healthy[] }>(done => { resolve = done; });
    });
    const { unmount } = render(<ConnectionPicker onRefresh={refresh} />);
    fireEvent.click(await screen.findByRole("button", { name: "Connect Gmail" }));
    fireEvent.click(await screen.findByRole("button", { name: "Cancel waiting" }));
    await act(async () => { resolve({ connections: [healthy] }); });
    expect(refresh).not.toHaveBeenCalled();
    expect(screen.getByText(/Stopped waiting/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Retry Google connection" }));
    await screen.findByRole("button", { name: "Cancel waiting" });
    unmount();
    const count = call.mock.calls.length;
    await act(async () => { resolve({ connections: [healthy] }); });
    expect(refresh).not.toHaveBeenCalled();
    expect(call.mock.calls.length).toBe(count);
  });

  it("rejects unsafe authorization URLs without opening a window", async () => {
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    vi.spyOn(api, "call").mockImplementation(async name => name === "google_client_get" ? { configured: true } : { ...authorization, authorize_url: "javascript:alert(1)" });
    render(<ConnectionPicker />);
    fireEvent.click(await screen.findByRole("button", { name: "Connect Gmail" }));
    expect(await screen.findByText(/did not return a valid Google authorization link/)).toBeVisible();
    expect(open).not.toHaveBeenCalled();
    expect(screen.queryByRole("link", { name: "Open Google authorization" })).not.toBeInTheDocument();
  });

  it("does not open a late authorization response after leaving setup", async () => {
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    let resolve!: (value: typeof authorization) => void;
    vi.spyOn(api, "call").mockImplementation(async name => name === "google_client_get" ? { configured: true } : new Promise<typeof authorization>(done => { resolve = done; }));
    const { unmount } = render(<ConnectionPicker />);
    fireEvent.click(await screen.findByRole("button", { name: "Connect Gmail" }));
    unmount();
    await act(async () => { resolve(authorization); });
    expect(open).not.toHaveBeenCalled();
  });

  it.each(["connection_start", "connections_list", "github_token_remove"] as const)("shows %s errors", async endpoint => {
    vi.spyOn(window, "open").mockReturnValue(null);
    vi.spyOn(api, "call").mockImplementation(async name => {
      if (name === endpoint) throw new ApiError(503, "unavailable", "The connection service is unavailable.");
      if (name === "google_client_get") return { configured: true };
      return authorization;
    });
    render(<ConnectionPicker />);
    fireEvent.click(await screen.findByRole("button", { name: endpoint === "github_token_remove" ? "Remove GitHub token" : "Connect Gmail" }));
    expect(await screen.findByText("The connection service is unavailable.")).toBeVisible();
  });
});
