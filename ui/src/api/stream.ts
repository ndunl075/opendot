import { streamPath, type ClientFrame, type StreamEvent } from "./endpoints.gen";
import { getApiToken } from "./client";
import { socketUrl } from "./connection";

type SocketFactory = (url: string, protocols: string[]) => WebSocket;

export class ChatStream {
  private socket?: WebSocket;
  private lastSeq = 0;

  constructor(
    private readonly conversationId: string,
    private readonly onEvent: (event: StreamEvent) => void,
    private readonly getToken: () => string | undefined = getApiToken,
    private readonly createSocket: SocketFactory = (url, protocols) => new WebSocket(url, protocols)
  ) {}

  connect(): void {
    const token = this.getToken();
    const protocols = token ? ["opendot", `opendot.bearer.${token}`] : ["opendot"];
    this.socket = this.createSocket(socketUrl(streamPath), protocols);
    this.socket.addEventListener("open", () => {
      if (this.lastSeq > 0) this.send({ type: "resume", conversation_id: this.conversationId, after_seq: this.lastSeq });
    });
    this.socket.addEventListener("message", (message) => {
      const event = JSON.parse(String(message.data)) as StreamEvent;
      if (typeof event.seq === "number") this.lastSeq = Math.max(this.lastSeq, event.seq);
      this.onEvent(event);
    });
  }

  send(frame: ClientFrame): void {
    this.socket?.send(JSON.stringify(frame));
  }

  close(): void { this.socket?.close(); }
  get sequence(): number { return this.lastSeq; }
}
