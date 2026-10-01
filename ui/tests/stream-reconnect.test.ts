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
  stream.connect(); expect(stream.send({ type: "send", text: "Hello" })).toBe(false);
  sockets[0].open(); stream.send({ type: "send", text: "Hello" });
  const event = { type: "text_delta", seq: 4, conversation_id: "assigned", message_id: "m", text: "Hi" };
  sockets[0].event(event); sockets[0].event(event); sockets[0].event({ ...event, conversation_id: "other", seq: 5 });
  expect(receive).toHaveBeenCalledTimes(1);
  sockets[0].close(); vi.advanceTimersByTime(1000); sockets[1].open();
  expect(sockets[1].sent).toEqual([JSON.stringify({ type: "resume", conversation_id: "assigned", after_seq: 4 })]);
  stream.close(); vi.advanceTimersByTime(60000); expect(sockets).toHaveLength(2);
});
