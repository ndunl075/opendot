import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createApi, getApiToken } from "../src/api/client";
import { ChatStream } from "../src/api/stream";

beforeEach(() => { document.head.innerHTML = '<meta name="opendot-api-token" content="web-token">'; });
afterEach(() => { delete window.__OPENDOT__; vi.unstubAllEnvs(); });

it("prefers desktop injection and sends HTTP and WebSocket to the same daemon", async () => {
  window.__OPENDOT__ = { token: "desktop-token", baseUrl: "http://127.0.0.1:8765/" };
  const fetcher = vi.fn().mockImplementation(async () => new Response('{"status":"ok"}'));
  await createApi(getApiToken, fetcher).call("health");
  expect(fetcher.mock.calls[0][0]).toBe("http://127.0.0.1:8765/v1/health");
  expect(new Headers(fetcher.mock.calls[0][1].headers).get("Authorization")).toBe("Bearer desktop-token");
  const socket = vi.fn(() => new EventTarget() as WebSocket);
  new ChatStream("c", vi.fn(), getApiToken, socket).connect();
  expect(socket).toHaveBeenCalledWith("ws://127.0.0.1:8765/v1/chat/stream", ["opendot", "opendot.bearer.desktop-token"]);
});

it("maps HTTPS to WSS, preserves base paths, and never puts a token in the URL", () => {
  window.__OPENDOT__ = { token: "desktop-token", baseUrl: "https://example.test/opendot/" };
  const socket = vi.fn(() => new EventTarget() as WebSocket);
  new ChatStream("c", vi.fn(), getApiToken, socket).connect();
  expect(socket.mock.calls[0]).toEqual(["wss://example.test/opendot/v1/chat/stream", ["opendot", "opendot.bearer.desktop-token"]]);
});

it("falls back to web meta then development settings and same-origin HTTP", async () => {
  vi.stubEnv("VITE_OPENDOT_TOKEN", "dev-token");
  expect(getApiToken()).toBe("web-token");
  document.head.innerHTML = "";
  expect(getApiToken()).toBe("dev-token");
  const fetcher = vi.fn().mockImplementation(async () => new Response('{"status":"ok"}'));
  await createApi(getApiToken, fetcher).call("health");
  expect(fetcher.mock.calls[0][0]).toBe("/v1/health");
  vi.stubEnv("VITE_OPENDOT_BASE_URL", "http://127.0.0.1:9876");
  await createApi(getApiToken, fetcher).call("health");
  expect(fetcher.mock.calls[1][0]).toBe("http://127.0.0.1:9876/v1/health");
});

it("rejects a malformed connection URL before sending credentials", async () => {
  window.__OPENDOT__ = { token: "desktop-token", baseUrl: "javascript:alert(1)" };
  const fetcher = vi.fn();
  await expect(createApi(getApiToken, fetcher).call("health")).rejects.toThrow(/HTTP/);
  expect(fetcher).not.toHaveBeenCalled();
});

it("keeps web sockets same-origin without a configured base", () => {
  const socket = vi.fn(() => new EventTarget() as WebSocket);
  new ChatStream("c", vi.fn(), getApiToken, socket).connect();
  const expected = new URL("/v1/chat/stream", window.location.href);
  expected.protocol = expected.protocol === "https:" ? "wss:" : "ws:";
  expect(socket).toHaveBeenCalledWith(expected.href, ["opendot", "opendot.bearer.web-token"]);
});

it("never reads development connection settings in a production build", async () => {
  vi.stubEnv("DEV", false);
  vi.stubEnv("VITE_OPENDOT_TOKEN", "dev-token");
  vi.stubEnv("VITE_OPENDOT_BASE_URL", "https://dev.example.test");
  document.head.innerHTML = "";
  expect(getApiToken()).toBeUndefined();
  const fetcher = vi.fn().mockImplementation(async () => new Response('{"status":"ok"}'));
  await createApi(getApiToken, fetcher).call("health");
  expect(fetcher.mock.calls[0][0]).toBe("/v1/health");
  expect(new Headers(fetcher.mock.calls[0][1].headers).has("Authorization")).toBe(false);
});
