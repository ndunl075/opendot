import { describe, expect, it, vi } from "vitest";
import { ChatStream } from "../src/api/stream";

class FakeSocket extends EventTarget {
  readonly sent: string[] = [];
  send(value: string) { this.sent.push(value); }
  close() {}
  emit(type: string, data?: unknown) { this.dispatchEvent(new MessageEvent(type, { data })); }
}

describe("chat stream", () => {
  it("uses bearer subprotocols, tracks seq, and resumes after reconnect", () => {
    const sockets: FakeSocket[] = [];
    const factory = vi.fn((...args: [string, string[]]) => {
      void args;
      const socket = new FakeSocket(); sockets.push(socket); return socket as unknown as WebSocket;
    });
    const stream = new ChatStream("conversation-1", vi.fn(), () => "token", factory);
    stream.connect();
    expect(factory.mock.calls[0][1]).toEqual(["opendot", "opendot.bearer.token"]);
    sockets[0].emit("message", JSON.stringify({ type: "text_delta", seq: 4, conversation_id: "conversation-1", message_id: "m", text: "hi" }));
    expect(stream.sequence).toBe(4);
    stream.connect();
    sockets[1].emit("open");
    expect(sockets[1].sent).toEqual([JSON.stringify({ type: "resume", conversation_id: "conversation-1", after_seq: 4 })]);
  });
});
