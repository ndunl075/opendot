import type { EndpointName, EndpointTable } from "./endpoints.gen";
import { apiUrl, getApiToken, redirectToLogin, requiresWebLogin } from "./connection";
export { getApiToken } from "./connection";

type PathParams = Record<string, string | number>;
type RequestFor<Name extends EndpointName> = EndpointTable[Name]["request"];
type CallOptions<Name extends EndpointName> = {
  params?: PathParams;
  body?: RequestFor<Name>;
};

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export function isStaleItemError(error: unknown): error is ApiError {
  return error instanceof ApiError && [404, 409, 410].includes(error.status);
}

export function isSessionError(error: unknown): error is ApiError {
  return error instanceof ApiError && (error.status === 401 || error.status === 1008 || error.code === "unauthorized");
}

function endpointPath(path: string, params: PathParams = {}): string {
  return path.replace(/\{([^}]+)\}/g, (_match, name: string) => {
    const value = params[name];
    if (value === undefined) throw new Error(`Missing path parameter: ${name}`);
    return encodeURIComponent(String(value));
  });
}

export function createApi(getToken = getApiToken, fetchImpl: typeof fetch = fetch, onLoginRequired = redirectToLogin) {
  async function call<Name extends EndpointName>(name: Name, options: CallOptions<Name> = {}): Promise<EndpointTable[Name]["response"]> {
    const endpoint = endpointTable[name];
    let url = apiUrl(endpointPath(endpoint.path, options.params));
    const headers = new Headers({ Accept: "application/json" });
    const token = getToken();
    if (!token && requiresWebLogin()) {
      onLoginRequired();
      throw new ApiError(401, "unauthorized", "Sign in required");
    }
    if (token) headers.set("Authorization", `Bearer ${token}`);

    const init: RequestInit = { method: endpoint.method, headers };
    if (endpoint.requestIn === "query" && options.body) {
      const query = new URLSearchParams();
      for (const [key, value] of Object.entries(options.body as object)) {
        if (value !== undefined && value !== null) query.set(key, String(value));
      }
      const encoded = query.toString();
      if (encoded) url += `?${encoded}`;
    } else if (endpoint.requestIn === "body") {
      headers.set("Content-Type", "application/json");
      init.body = JSON.stringify(options.body ?? {});
    }

    const response = await fetchImpl(url, init);
    if (!response.ok) {
      const error = await response.json().catch(() => ({ code: "http_error", message: response.statusText })) as { code?: string; message?: string };
      if (response.status === 401 && requiresWebLogin()) onLoginRequired();
      throw new ApiError(response.status, error.code ?? "http_error", error.message ?? response.statusText);
    }
    return response.json() as Promise<EndpointTable[Name]["response"]>;
  }

  return { call };
}

import { endpointTable } from "./endpoints.gen";
export const api = createApi();
