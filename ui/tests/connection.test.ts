import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createApi, getApiToken } from "../src/api/client";
import { signOut } from "../src/api/connection";
import { ChatStream } from "../src/api/stream";

beforeEach(() => { window.localStorage.clear(); });
afterEach(() => { delete window.__OPENDOT__; window.localStorage.clear(); vi.unstubAllEnvs(); });

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

it("uses desktop, then local storage, then development token sources and same-origin HTTP", async () => {
  vi.stubEnv("VITE_OPENDOT_TOKEN", "dev-token");
  document.head.innerHTML = '<meta name="opendot-api-token" content="never-read">';
  expect(getApiToken()).toBe("dev-token");
  window.localStorage.setItem("opendot.token", "web-token");
  expect(getApiToken()).toBe("web-token");
  window.__OPENDOT__ = { token: "desktop-token" };
  expect(getApiToken()).toBe("desktop-token");
  delete window.__OPENDOT__;
  window.localStorage.removeItem("opendot.token");
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
  window.localStorage.setItem("opendot.token", "web-token");
  const socket = vi.fn(() => new EventTarget() as WebSocket);
  new ChatStream("c", vi.fn(), getApiToken, socket).connect();
  const expected = new URL("/v1/chat/stream", window.location.href);
  expected.protocol = expected.protocol === "https:" ? "wss:" : "ws:";
  expect(socket).toHaveBeenCalledWith(expected.href, ["opendot", "opendot.bearer.web-token"]);
});

it("redirects a production web session without a stored token before requesting the API", async () => {
  vi.stubEnv("DEV", false);
  vi.stubEnv("VITE_OPENDOT_TOKEN", "dev-token");
  vi.stubEnv("VITE_OPENDOT_BASE_URL", "https://dev.example.test");
  expect(getApiToken()).toBeUndefined();
  const fetcher = vi.fn();
  const login = vi.fn();
  await expect(createApi(getApiToken, fetcher, login).call("health")).rejects.toMatchObject({ status: 401 });
  expect(login).toHaveBeenCalledOnce();
  expect(fetcher).not.toHaveBeenCalled();
});

it("redirects a production web session after a 401 response", async () => {
  vi.stubEnv("DEV", false);
  window.localStorage.setItem("opendot.token", "expired-token");
  const fetcher = vi.fn().mockResolvedValue(new Response('{"code":"unauthorized","message":"Expired"}', { status: 401 }));
  const login = vi.fn();
  await expect(createApi(getApiToken, fetcher, login).call("health")).rejects.toMatchObject({ status: 401 });
  expect(login).toHaveBeenCalledOnce();
});

it("does not redirect the Vite mock server or desktop shell", async () => {
  const fetcher = vi.fn().mockImplementation(() => Promise.resolve(new Response('{"status":"ok"}')));
  const login = vi.fn();
  await createApi(() => undefined, fetcher, login).call("health");
  expect(login).not.toHaveBeenCalled();
  window.__OPENDOT__ = {};
  vi.stubEnv("DEV", false);
  await createApi(() => undefined, fetcher, login).call("health");
  expect(login).not.toHaveBeenCalled();
});

it("signs out through the daemon only for the web app", () => {
  const webLocation = { assign: vi.fn() };
  signOut(webLocation);
  expect(webLocation.assign).toHaveBeenCalledWith("/logout");
  window.__OPENDOT__ = { token: "desktop-token" };
  const desktopLocation = { assign: vi.fn() };
  signOut(desktopLocation);
  expect(desktopLocation.assign).not.toHaveBeenCalled();
});
