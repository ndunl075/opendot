import { useEffect, useRef, useState } from "react";
import { api } from "../../api/client";
import type { Connection } from "../../api/types.gen";
import { Button, ErrorState } from "../../design/components";
import { safeExternalUrl } from "../shared";

/** A two-minute, non-overlapping poll. Cleanup ignores late requests after cancel or unmount. */
export function GoogleAuthorization({ app, authorizeUrl, connectionId, onRefresh, onRetry }: {
  app: Connection["app"]; authorizeUrl: string; connectionId?: string; onRefresh?: () => void; onRetry?: () => void;
}) {
  const url = safeExternalUrl(authorizeUrl);
  const [status, setStatus] = useState<"waiting" | "done" | "cancelled" | "expired" | "error">("waiting");
  const [error, setError] = useState<unknown>();
  const [attempt, setAttempt] = useState(0);
  const refresh = useRef(onRefresh);
  const check = useRef<() => void>(() => {});
  useEffect(() => { refresh.current = onRefresh; }, [onRefresh]);
  useEffect(() => {
    if (!url || status !== "waiting") return;
    let alive = true;
    let busy = false;
    let timer: ReturnType<typeof setTimeout>;
    const deadline = setTimeout(() => { alive = false; clearTimeout(timer); setStatus("expired"); }, 120_000);
    async function poll() {
      if (!alive || busy) return;
      clearTimeout(timer); busy = true;
      try {
        const result = await api.call("connections_list");
        if (!alive) return;
        const ready = result.connections.some(item => item.app === app && item.health === "ok" && (!connectionId || (item.id === connectionId && item.write_opt_in && item.read_only === false)));
        if (ready) { setStatus("done"); refresh.current?.(); }
        else timer = setTimeout(() => void poll(), 2000);
      } catch (cause) { if (alive) { setError(cause); setStatus("error"); } }
      finally { busy = false; }
    }
    check.current = () => void poll();
    void poll();
    return () => { alive = false; clearTimeout(timer); clearTimeout(deadline); check.current = () => {}; };
  }, [url, app, connectionId, status, attempt]);
  function retry() { onRetry?.(); setError(undefined); setStatus("waiting"); setAttempt(value => value + 1); }
  const label = app === "gmail" ? "Gmail" : "Google Calendar";
  if (!url) return <div className="notice"><p role="alert">The daemon did not return a valid Google authorization link.</p>{onRetry && <Button variant="secondary" onClick={retry}>Retry Google connection</Button>}</div>;
  return <div className="notice form-stack">
    <p role="status">{status === "waiting" ? `Waiting for Google. Finish ${connectionId ? "granting write access" : `connecting ${label}`} in your browser. OpenDot checks for up to two minutes.` : status === "done" ? connectionId ? "Google write access is granted. Every draft or event still needs your approval in OpenDot." : `${label} is connected and healthy.` : status === "cancelled" ? "Stopped waiting. This does not revoke access already granted in Google." : status === "expired" ? "Google has not confirmed access yet. You can retry when you are ready." : "Could not check Google access. Retry to check again."}</p>
    {status !== "done" && <>
      <a href={url} target="_blank" rel="noopener noreferrer">{connectionId ? "Grant access in Google" : "Open Google authorization"}</a>
      <p>If no browser tab opened, use the link above. Return here after granting access.</p>
      {error != null && <ErrorState error={error} />}
      <div className="actions">{status === "waiting" ? <><Button variant="secondary" onClick={() => check.current()}>Check Google access</Button><Button variant="ghost" onClick={() => setStatus("cancelled")}>Cancel waiting</Button></> : <Button variant="secondary" onClick={retry}>Retry Google connection</Button>}</div>
    </>}
  </div>;
}
