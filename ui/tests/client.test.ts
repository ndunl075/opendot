import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, createApi } from "../src/api/client";

describe("api client", () => {
  beforeEach(() => document.head.innerHTML = '<meta name="opendot-api-token" content="secret">');

  it("interpolates and encodes path parameters", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: "ok" }), { status: 200 }));
    const client = createApi(() => undefined, fetchMock);
    await client.call("chat_conversation", { params: { conversation_id: "a/b" } });
    expect(fetchMock.mock.calls[0][0]).toBe("/v1/chat/conversations/a%2Fb");
  });

  it("sends a bearer token when known", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: "ok" }), { status: 200 }));
    const client = createApi(() => "secret", fetchMock);
    await client.call("health");
    expect(new Headers(fetchMock.mock.calls[0][1].headers).get("Authorization")).toBe("Bearer secret");
  });

  it("maps contract errors to ApiError", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: "forbidden", message: "Nope" }), { status: 403 }));
    const client = createApi(() => undefined, fetchMock);
    await expect(client.call("health")).rejects.toMatchObject({ status: 403, code: "forbidden", message: "Nope" } satisfies Partial<ApiError>);
  });
});
