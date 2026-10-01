import { afterEach, expect, it, vi } from "vitest";
import { ChatStream } from "../src/api/stream";
class Socket extends EventTarget {
  sent: string[] = [];
  send(value: string) { this.sent.push(value); }
  close() { this.dispatchEvent(new Event("close")); }
  open() { this.dispatchEvent(new Event("open")); }
  event(value: unknown) { this.dispatchEvent(new MessageEvent("message", { data: JSON.stringify(value) })); }
}
afterEach(() => vi.useRealTimers());
it("reconnects, resumes the assigned conversation, and ignores duplicate/foreign events", () => {
  vi.useFakeTimers(); const sockets: Socket[] = []; const receive = vi.fn();
  const stream = new ChatStream("", receive, () => undefined, () => { const s = new Socket(); sockets.push(s); return s as unknown as WebSocket; });
  stream.connect(); sockets[0].open();
  sockets[0].event({ type: "text_delta", seq: 1, conversation_id: "foreign", message_id: "m", text: "Foreign reply" });
  expect(receive).not.toHaveBeenCalled();
  stream.subscribe("assigned");
  const event = { type: "text_delta", seq: 4, conversation_id: "assigned", message_id: "m", text: "Hi" };
  sockets[0].event(event); sockets[0].event(event); sockets[0].event({ ...event, conversation_id: "other", seq: 5 });
  expect(receive).toHaveBeenCalledTimes(1);
  sockets[0].close(); vi.advanceTimersByTime(1000); sockets[1].open();
  expect(sockets[1].sent).toEqual([JSON.stringify({ type: "resume", conversation_id: "assigned", after_seq: 4 })]);
  stream.close(); vi.advanceTimersByTime(60000); expect(sockets).toHaveLength(2);
});

it("stops retries on an expired session", () => {
  vi.useFakeTimers(); const socket = new Socket(); const factory = vi.fn(() => socket as unknown as WebSocket); const state = vi.fn();
  const stream = new ChatStream("c", vi.fn(), () => undefined, factory, state);
  stream.connect(); socket.open(); socket.dispatchEvent(new CloseEvent("close", { code: 1008 }));
  vi.advanceTimersByTime(120000);
  expect(state).toHaveBeenLastCalledWith("unauthorized"); expect(factory).toHaveBeenCalledTimes(1);
  stream.close();
});

it("bounds consecutive failures even if a socket opens before dropping", () => {
  vi.useFakeTimers(); const sockets: Socket[] = []; const state = vi.fn();
  const stream = new ChatStream("c", vi.fn(), () => undefined, () => { const socket = new Socket(); sockets.push(socket); return socket as unknown as WebSocket; }, state);
  stream.connect();
  for (let i = 0; i < 6; i++) { sockets.at(-1)!.open(); sockets.at(-1)!.close(); vi.advanceTimersByTime(10000); }
  expect(state).toHaveBeenLastCalledWith("failed"); expect(sockets).toHaveLength(6);
  vi.advanceTimersByTime(120000); expect(sockets).toHaveLength(6); stream.close();
});
