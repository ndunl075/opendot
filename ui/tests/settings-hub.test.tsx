import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import { App } from "../src/App";
import { api } from "../src/api/client";
import { ThemeProvider } from "../src/design/theme";
import { ScreenHeading, SettingsPaneContext } from "../src/screens/shared";

vi.mock("../src/screens/settings/SettingsScreen", () => ({ default: () => <p>General controls</p>, AvailabilityScreen: () => <p>Availability controls</p>, ModelsScreen: () => <p>Model controls</p>, ProvidersScreen: () => <p>Provider controls</p>, BackupScreen: () => <p>Backup controls</p> }));
vi.mock("../src/screens/companion/CompanionScreen", () => ({ default: () => <p>Companion controls</p> }));
vi.mock("../src/screens/connections/ConnectionsScreen", () => ({ default: () => <p>Connections controls</p> }));
vi.mock("../src/screens/memory", () => ({ default: () => <p>Memory controls</p> }));
vi.mock("../src/screens/rules", () => ({ default: () => <p>Rules controls</p> }));
vi.mock("../src/screens/usage/UsageScreen", () => ({ default: () => <p>Usage controls</p> }));
vi.mock("../src/screens/activity", () => ({ default: () => <p>Activity controls</p> }));
vi.mock("../src/screens/chat/ChatScreen", () => ({ default: () => <h1>Chat</h1> }));

const labels = ["General", "Availability", "Models", "Providers", "Backup", "Companion", "Connections", "Memory", "Rules", "Usage", "Activity", "About"];
function Location() { const location = useLocation(); return <output data-testid="location">{location.pathname}{location.search}{location.hash}</output>; }
function mount(path: string) {
  render(<ThemeProvider><MemoryRouter initialEntries={[path]}><App /><Location /></MemoryRouter></ThemeProvider>);
}
beforeEach(() => {
  vi.spyOn(api, "call").mockResolvedValue({ current_step: "done", completed_steps: ["done"], status: "ok" } as never);
});

it("orders the settings navigation and focuses the heading when sections change", async () => {
  mount("/settings/general");
  const navigation = await screen.findByRole("navigation", { name: "Settings sections" });
  expect(within(navigation).getAllByRole("list")).toHaveLength(1);
  expect(within(navigation).getAllByRole("link").map(link => link.textContent)).toEqual(labels);
  for (const label of labels) {
    fireEvent.click(within(navigation).getByRole("link", { name: label }));
    await waitFor(() => expect(screen.getByRole("heading", { name: label, level: 1 })).toHaveFocus());
    expect(within(navigation).getByRole("link", { name: label })).toHaveAttribute("aria-current", "page");
    expect(navigation.querySelectorAll('[aria-current="page"]')).toHaveLength(1);
    expect(screen.getByTestId("location")).toHaveTextContent(`/settings/${label.toLowerCase()}`);
  }
});

it.each(labels.slice(1))("redirects legacy %s URLs without losing query or hash", async label => {
  const section = label.toLowerCase();
  mount(`/${section}?source=notification#item%2Ftarget`);
  await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent(`/settings/${section}?source=notification#item%2Ftarget`));
  expect(await screen.findByRole("heading", { name: label, level: 1 })).toBeInTheDocument();
});

it.each(labels)("opens the %s section directly", async label => {
  mount(`/settings/${label.toLowerCase()}`);
  expect(await screen.findByRole("heading", { name: label, level: 1 })).toHaveFocus();
  expect(screen.getByRole("link", { name: label })).toHaveAttribute("aria-current", "page");
});

it("sends a completed user's onboarding link to Chat", async () => {
  mount("/onboarding");
  await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("/chat"));
  expect(await screen.findByRole("heading", { name: "Chat" })).toBeInTheDocument();
});

it("keeps About available before setup and omits unsupported setup restart", async () => {
  vi.mocked(api.call).mockResolvedValue({ current_step: "companion", completed_steps: [], status: "ok" } as never);
  mount("/about");
  expect(await screen.findByRole("heading", { name: "About", level: 1 })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Run setup again" })).not.toBeInTheDocument();
});

it("opens General at the desktop settings index", async () => {
  mount("/settings");
  expect(await screen.findByRole("heading", { name: "General", level: 1 })).toHaveFocus();
  expect(screen.getByRole("link", { name: "General" })).toHaveAttribute("aria-current", "page");
  expect(screen.getByText("General controls")).toBeInTheDocument();
});

it("focuses the mobile directory after returning from a section", async () => {
  Object.defineProperty(window.matchMedia("(max-width: 800px)"), "matches", { value: true });
  mount("/settings");
  expect(await screen.findByRole("heading", { name: "Settings", level: 1 })).toHaveFocus();
  fireEvent.click(screen.getByRole("link", { name: "Connections" }));
  expect(await screen.findByRole("heading", { name: "Connections", level: 1 })).toHaveFocus();
  fireEvent.click(screen.getByRole("link", { name: "Back to Settings" }));
  expect(screen.getByRole("heading", { name: "Settings", level: 1 })).toHaveFocus();
  expect(screen.getByTestId("location")).toHaveTextContent(/^\/settings$/);
});

it("lets an unknown section recover to settings", async () => {
  mount("/settings/missing");
  fireEvent.click(await screen.findByRole("link", { name: "Back to Settings" }));
  expect(await screen.findByRole("heading", { name: "General", level: 1 })).toHaveFocus();
});

it("removes the embedded page header while keeping its action", () => {
  render(<SettingsPaneContext.Provider value={true}><ScreenHeading title="Activity" description="Page description" action={<button>Refresh activity</button>} /></SettingsPaneContext.Provider>);
  expect(screen.queryByRole("heading")).not.toBeInTheDocument();
  expect(screen.queryByText("Page description")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Refresh activity" })).toBeInTheDocument();
});
