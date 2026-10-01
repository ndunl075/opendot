declare global {
  interface Window {
    /** Injected by the desktop shell before loading the UI. Never persisted. */
    __OPENDOT__?: { token?: string; baseUrl?: string };
  }
}

export function getApiToken(): string | undefined {
  const desktopToken = window.__OPENDOT__?.token;
  if (desktopToken) return desktopToken;
  try {
    const webToken = window.localStorage.getItem("opendot.token");
    if (webToken) return webToken;
  } catch {
    // Storage can be unavailable in private or restricted browser contexts.
  }
  return import.meta.env.DEV ? import.meta.env.VITE_OPENDOT_TOKEN || undefined : undefined;
}

export function isWebApp(): boolean {
  return !window.__OPENDOT__;
}

/** The Vite mock server intentionally accepts unauthenticated development requests. */
export function requiresWebLogin(): boolean {
  return isWebApp() && !import.meta.env.DEV;
}

export function redirectToLogin(location: Pick<Location, "assign"> = window.location): void {
  location.assign("/login");
}

export function signOut(location: Pick<Location, "assign"> = window.location): void {
  if (isWebApp()) location.assign("/logout");
}

/** Empty means same origin (including Vite's development proxy). */
export function getApiBaseUrl(): string {
  const base = window.__OPENDOT__?.baseUrl ?? (import.meta.env.DEV ? import.meta.env.VITE_OPENDOT_BASE_URL : undefined);
  if (!base) return "";
  const url = new URL(base);
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
    throw new Error("OpenDot requires an HTTP(S) base URL without credentials, query or fragment.");
  }
  return url.href.replace(/\/+$/, "");
}

export function apiUrl(path: string): string { return `${getApiBaseUrl()}${path}`; }

export function socketUrl(path: string): string {
  const url = new URL(apiUrl(path), window.location.href);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.href;
}
