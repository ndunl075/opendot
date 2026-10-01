import { streamPath, type ClientFrame, type StreamEvent } from "./endpoints.gen";
import { getApiToken } from "./client";
import { socketUrl } from "./connection";

type SocketFactory = (url: string, protocols: string[]) => WebSocket;
export type StreamState = "connecting" | "connected" | "reconnecting" | "closed";

export class ChatStream {
  private socket?: WebSocket;
  private lastSeq = 0;
  private ready = false;
  private disposed = false;
  private retry?: ReturnType<typeof setTimeout>;
  private attempts = 0;
  private awaitingConversation = false;

  constructor(
    private conversationId: string,
    private readonly onEvent: (event: StreamEvent) => void,
    private readonly getToken: () => string | undefined = getApiToken,
    private readonly createSocket: SocketFactory = (url, protocols) => new WebSocket(url, protocols),
    private readonly onState: (state: StreamState) => void = () => {}
  ) {}

  connect(): void {
    clearTimeout(this.retry);
    this.disposed = false;
    this.ready = false;
    const previous = this.socket;
    this.socket = undefined;
    previous?.close();
    this.onState(this.attempts ? "reconnecting" : "connecting");
    const token = this.getToken();
    const protocols = token ? ["opendot", `opendot.bearer.${token}`] : ["opendot"];
    const socket = this.createSocket(socketUrl(streamPath), protocols);
    this.socket = socket;
    socket.addEventListener("open", () => {
      if (this.socket !== socket || this.disposed) return;
      this.ready = true; this.attempts = 0; this.onState("connected");
      if (this.conversationId) this.send({ type: "resume", conversation_id: this.conversationId, after_seq: this.lastSeq });
    });
    socket.addEventListener("message", (message) => {
      if (this.socket !== socket || this.disposed) return;
      let event: StreamEvent;
      try { event = JSON.parse(String(message.data)) as StreamEvent; } catch { return; }
      if (!event || typeof event !== "object" || typeof event.seq !== "number" || typeof event.conversation_id !== "string") return;
      if (!this.conversationId && this.awaitingConversation && event.conversation_id) this.conversationId = event.conversation_id;
      if (event.conversation_id !== this.conversationId) return;
      if (event.seq > 0 && event.seq <= this.lastSeq) return;
      this.lastSeq = Math.max(this.lastSeq, event.seq);
      this.onEvent(event);
    });
    socket.addEventListener("close", () => {
      if (this.socket !== socket || this.disposed) return;
      this.ready = false; this.onState("reconnecting");
      this.retry = setTimeout(() => this.connect(), Math.min(1000 * 2 ** this.attempts++, 10000));
    });
    // A failed WebSocket emits close after error. Reconnect only there, once.
  }

  send(frame: ClientFrame): boolean {
    if (!this.ready || !this.socket) return false;
    if (frame.type === "send") this.awaitingConversation = true;
    this.socket.send(JSON.stringify(frame));
    return true;
  }

  close(): void { this.disposed = true; this.ready = false; clearTimeout(this.retry); this.socket?.close(); this.onState("closed"); }
  get sequence(): number { return this.lastSeq; }
}
