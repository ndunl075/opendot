import { streamPath, type ClientFrame, type StreamEvent } from "./endpoints.gen";
import { getApiToken } from "./client";
import { socketUrl } from "./connection";

type SocketFactory = (url: string, protocols: string[]) => WebSocket;
export type StreamState = "connecting" | "connected" | "reconnecting" | "closed" | "unauthorized" | "failed";

export class ChatStream {
  private socket?: WebSocket;
  private lastSeq = 0;
  private ready = false;
  private disposed = false;
  private retry?: ReturnType<typeof setTimeout>;
  private attempts = 0;
  private connectedAt = 0;
  private watchdog?: ReturnType<typeof setTimeout>;

  constructor(
    private conversationId: string,
    private readonly onEvent: (event: StreamEvent) => void,
    private readonly getToken: () => string | undefined = getApiToken,
    private readonly createSocket: SocketFactory = (url, protocols) => new WebSocket(url, protocols),
    private readonly onState: (state: StreamState) => void = () => {}
  ) {}

  connect(): void {
    clearTimeout(this.retry);
    clearTimeout(this.watchdog);
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
      clearTimeout(this.watchdog);
      this.ready = true; this.connectedAt = Date.now(); this.onState("connected");
      if (this.conversationId) this.send({ type: "resume", conversation_id: this.conversationId, after_seq: this.lastSeq });
    });
    socket.addEventListener("message", (message) => {
      if (this.socket !== socket || this.disposed) return;
      let event: StreamEvent;
      try { event = JSON.parse(String(message.data)) as StreamEvent; } catch { return; }
      if (!event || typeof event !== "object" || typeof event.seq !== "number" || typeof event.conversation_id !== "string") return;
      if (!this.conversationId || event.conversation_id !== this.conversationId) return;
      if (event.seq > 0 && event.seq <= this.lastSeq) return;
      this.lastSeq = Math.max(this.lastSeq, event.seq);
      this.onEvent(event);
    });
    socket.addEventListener("close", (event) => {
      if (this.socket !== socket || this.disposed) return;
      clearTimeout(this.watchdog);
      if (this.ready && Date.now() - this.connectedAt >= 30000) this.attempts = 0;
      this.ready = false;
      if (event.code === 1008) { this.onState("unauthorized"); return; }
      this.reconnect();
    });
    this.watchdog = setTimeout(() => {
      if (this.socket !== socket || this.disposed || this.ready) return;
      this.socket = undefined; socket.close(); this.reconnect();
    }, 15000);
    // A failed WebSocket emits close after error. Reconnect only there, once.
  }

  private reconnect(): void {
    if (this.attempts >= 5) { this.onState("failed"); return; }
    this.onState("reconnecting");
    this.retry = setTimeout(() => this.connect(), Math.min(1000 * 2 ** this.attempts++, 10000));
  }

  /** Only bind IDs supplied by REST, never IDs from broadcast events. */
  subscribe(conversationId: string): void {
    if (this.conversationId !== conversationId) this.lastSeq = 0;
    this.conversationId = conversationId;
    if (conversationId) this.send({ type: "resume", conversation_id: conversationId, after_seq: this.lastSeq });
  }

  send(frame: ClientFrame): boolean {
    if (!this.ready || !this.socket) return false;
    if (frame.type === "send") return false; // Messages use REST to obtain their authoritative IDs.
    this.socket.send(JSON.stringify(frame));
    return true;
  }

  close(): void { this.disposed = true; this.ready = false; clearTimeout(this.retry); clearTimeout(this.watchdog); this.socket?.close(); this.onState("closed"); }
  get sequence(): number { return this.lastSeq; }
}
