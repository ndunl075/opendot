import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../../api/client";
import type { ConnectionStart, ConnectionStartRequest } from "../../api/types.gen";
import { Button, ErrorState, Skeleton } from "../../design/components";
import { safeExternalUrl, useMutation, useResource } from "../shared";
import { GoogleSetup, GithubSetup } from "./CredentialForms";
import { GoogleAuthorization } from "./GoogleAuthorization";

export const appLabels: Record<ConnectionStartRequest["app"], string> = { gmail: "Gmail", google_calendar: "Google Calendar", github: "GitHub" };
const loadGoogle = () => api.call("google_client_get");

/** Starting a connection requests read access only. Write access has its own opt-in. */
export function ConnectionPicker({ onRefresh }: { onRefresh?: () => void }) {
  const google = useResource(loadGoogle);
  const mutation = useMutation();
  const [started, setStarted] = useState<ConnectionStart>();
  const [attempt, setAttempt] = useState(0);
  const active = useRef(false);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  async function connect(app: "gmail" | "google_calendar") {
    setStarted(undefined);
    const result = await mutation.run(async () => {
      try { return await api.call("connection_start", { body: { app } }); }
      catch (error) {
        if (active.current && error instanceof ApiError && error.code === "google_client_missing") {
          google.setData(previous => ({ ...previous, configured: false }));
          void google.reload();
        }
        throw error;
      }
    });
    if (result && active.current) {
      setStarted({ ...result, app }); setAttempt(value => value + 1);
      const url = safeExternalUrl(result.authorize_url);
      if (url) window.open(url, "_blank", "noopener,noreferrer");
    }
  }
  return <div className="form-stack">
    <p className="muted">Connecting is optional. OpenDot asks Google for read-only access by default. Creating Gmail drafts or Calendar events is a separate opt-in, and each still needs your approval in OpenDot.</p>
    {google.loading ? <Skeleton label="Checking Google setup" /> : google.error ? <ErrorState error={google.error} onRetry={google.reload} /> : google.data && <>
      {!google.data.configured ? <GoogleSetup status={google.data} onSave={google.setData} /> : <section className="form-stack" aria-label="Connect Google apps">
        <h3>Connect Google apps</h3><p className="muted">Your Google client is set up. Choose an app, then finish granting read access in your browser.</p>
        <div className="actions"><Button variant="secondary" disabled={mutation.pending} onClick={() => void connect("gmail")}>Connect Gmail</Button><Button variant="secondary" disabled={mutation.pending} onClick={() => void connect("google_calendar")}>Connect Google Calendar</Button></div>
      </section>}
    </>}
    {mutation.error != null && !(mutation.error instanceof ApiError && mutation.error.code === "google_client_missing" && google.data?.configured) && <ErrorState error={mutation.error} />}
    {started && <GoogleAuthorization key={attempt} app={started.app} authorizeUrl={started.authorize_url} onRefresh={onRefresh} onRetry={() => void connect(started.app === "gmail" ? "gmail" : "google_calendar")} />}
    <GithubSetup onRefresh={onRefresh} />
    {onRefresh && <Button variant="ghost" onClick={onRefresh}>Refresh accounts</Button>}
  </div>;
}
