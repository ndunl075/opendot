import { act, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../src/api/client";
import type { PausedEvent } from "../src/api/types.gen";
import { PausedBanner } from "../src/screens/chat/PausedBanner";

class Socket extends EventTarget {
  static instances: Socket[] = [];
  sent: string[] = [];
  constructor() { super(); Socket.instances.push(this); }
  send(value: string) { this.sent.push(value); }
  close() {}
  event(value: unknown) { this.dispatchEvent(new MessageEvent("message", { data: JSON.stringify(value) })); }
}
const pause: PausedEvent = { type: "paused", reason: "task_budget", message: "Paused", conversation_id: "c", message_id: "m", task_id: "task", seq: 4 };
afterEach(() => { vi.unstubAllGlobals(); Socket.instances = []; });
it("confirms only the matching task pause via fresh replay, then invalidates on task progress", () => {
  vi.stubGlobal("WebSocket", Socket);
  const call = vi.spyOn(api, "call");
  render(<MemoryRouter><PausedBanner event={pause} /></MemoryRouter>);
  const socket = Socket.instances[0];
  act(() => socket.dispatchEvent(new Event("open")));
  expect(socket.sent.map(value => JSON.parse(value))).toEqual([{ type: "resume", conversation_id: "c", after_seq: 0 }]);
  expect(screen.queryByRole("button", { name: "Continue anyway" })).not.toBeInTheDocument();
  act(() => socket.event({ ...pause, conversation_id: "other" }));
  expect(screen.queryByRole("button", { name: "Continue anyway" })).not.toBeInTheDocument();
  act(() => socket.event(pause));
  expect(screen.getByRole("button", { name: "Continue anyway" })).toBeEnabled();
  act(() => socket.event({ type: "message_started", conversation_id: "c", message_id: "m", seq: 5 }));
  expect(screen.queryByRole("button", { name: "Continue anyway" })).not.toBeInTheDocument();
  expect(call).not.toHaveBeenCalled();
});

it("stops offering a resume when the verification socket loses authorization", () => {
  vi.stubGlobal("WebSocket", Socket);
  render(<MemoryRouter><PausedBanner event={pause} /></MemoryRouter>);
  const socket = Socket.instances[0];
  act(() => { socket.dispatchEvent(new Event("open")); socket.event(pause); });
  act(() => socket.dispatchEvent(new CloseEvent("close", { code: 1008 })));
  expect(screen.queryByRole("button", { name: "Continue anyway" })).not.toBeInTheDocument();
  expect(screen.getByText("Your session expired")).toBeVisible();
});
