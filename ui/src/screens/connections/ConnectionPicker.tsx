import { useState } from "react";
import { api } from "../../api/client";
import type { ConnectionStart, ConnectionStartRequest } from "../../api/types.gen";
import { Button, ErrorState } from "../../design/components";
import { safeExternalUrl, useMutation } from "../shared";

export const appLabels: Record<ConnectionStartRequest["app"], string> = { gmail: "Gmail", google_calendar: "Calendar", github: "GitHub" };

/** Starting a connection requests read access only. Write access has its own opt-in. */
export function ConnectionPicker({ onRefresh }: { onRefresh?: () => void }) {
  const mutation = useMutation();
  const [started, setStarted] = useState<ConnectionStart>();
  async function connect(app: ConnectionStartRequest["app"]) {
    const result = await mutation.run(() => api.call("connection_start", { body: { app } }));
    if (result) setStarted(result);
  }
  return <div className="form-stack">
    <p className="muted">Choose what your companion can read. Connecting is optional. Sending messages is not enabled.</p>
    <div className="actions">{(Object.keys(appLabels) as ConnectionStartRequest["app"][]).map(app => <Button key={app} variant="secondary" disabled={mutation.pending} onClick={() => void connect(app)}>Connect {appLabels[app]}</Button>)}</div>
    {mutation.error != null && <ErrorState error={mutation.error} />}
    {started && <div className="notice" role="status"><p>Finish connecting {appLabels[started.app]} in your browser, then refresh accounts.</p>
      {safeExternalUrl(started.authorize_url) ? <a href={safeExternalUrl(started.authorize_url)} target="_blank" rel="noreferrer">Continue to {appLabels[started.app]} authorization</a> : <p>The daemon did not return a valid authorization link. Try connecting again.</p>}
    </div>}
    {onRefresh && <Button variant="ghost" onClick={onRefresh}>Refresh accounts</Button>}
  </div>;
}
