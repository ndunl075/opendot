import { useEffect, useRef, useState, type FormEvent } from "react";
import { api } from "../../api/client";
import type { GoogleClientStatus } from "../../api/types.gen";
import { Button, ErrorState, Input } from "../../design/components";
import { useMutation } from "../shared";

const googleSteps = [
  ["Create a project", "https://console.cloud.google.com/projectcreate"],
  ["Enable Gmail API", "https://console.cloud.google.com/apis/library/gmail.googleapis.com"],
  ["Enable Google Calendar API", "https://console.cloud.google.com/apis/library/calendar-json.googleapis.com"],
  ["Consent screen (External, add yourself as a test user)", "https://console.cloud.google.com/auth/overview"],
  ['Create an OAuth client, type "Desktop app", then click "Download JSON"', "https://console.cloud.google.com/auth/clients/create"],
];

function parseGoogleClient(text: string) {
  const invalid = "Choose a Google client JSON file containing a client ID and secret.";
  let value: unknown;
  try { value = JSON.parse(text); } catch { throw new Error(invalid); }
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(invalid);
  const isWeb = !("installed" in value) && "web" in value;
  const client = "installed" in value ? value.installed : "web" in value ? value.web : undefined;
  if (!client || typeof client !== "object" || Array.isArray(client)
    || !("client_id" in client) || typeof client.client_id !== "string" || !client.client_id.trim()
    || !("client_secret" in client) || typeof client.client_secret !== "string" || !client.client_secret.trim()) throw new Error(invalid);
  return { clientId: client.client_id.trim(), secret: client.client_secret.trim(), isWeb };
}

export function GoogleSetup({ status, onSave }: { status: GoogleClientStatus; onSave: (status: GoogleClientStatus) => void }) {
  const [clientId, setClientId] = useState("");
  const [secret, setSecret] = useState("");
  const [replacing, setReplacing] = useState(false);
  const [reading, setReading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [isWeb, setIsWeb] = useState(false);
  const [uploaded, setUploaded] = useState(false);
  const uploadRef = useRef<HTMLInputElement>(null);
  const replaceRef = useRef<HTMLButtonElement>(null);
  const moveFocus = useRef(false);
  const mutation = useMutation();
  const collapsed = status.configured && !replacing;
  useEffect(() => {
    if (moveFocus.current) {
      (collapsed ? replaceRef.current : uploadRef.current)?.focus();
      moveFocus.current = false;
    }
  }, [collapsed]);
  async function upload(file: File) {
    setClientId(""); setSecret(""); setUploadError(""); setIsWeb(false); setUploaded(false);
    if (file.size > 64 * 1024) { setUploadError("Choose a JSON file no larger than 64 KB."); return; }
    setReading(true);
    try {
      const text = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result));
        reader.onerror = () => reject(new Error("The file could not be read. Choose it again."));
        reader.readAsText(file);
      });
      const client = parseGoogleClient(text);
      setClientId(client.clientId); setSecret(client.secret); setIsWeb(client.isWeb); setUploaded(true);
    } catch (error) { setUploadError(error instanceof Error ? error.message : "The file could not be read. Choose it again."); }
    finally { setReading(false); }
  }
  async function save(event: FormEvent) {
    event.preventDefault();
    if (reading || mutation.pending || !clientId.trim() || !secret.trim()) return;
    const body = { client_id: clientId.trim(), client_secret: secret.trim() };
    setSecret(""); setUploaded(false);
    const result = await mutation.run(() => api.call("google_client_set", { body }));
    if (result) {
      if (result.configured) { moveFocus.current = true; setReplacing(false); setClientId(""); setIsWeb(false); }
      onSave(result);
      if (!result.configured) mutation.setNotice("The Google client was not saved. Check the setup steps and try again.");
    }
  }
  if (collapsed) return <div className="actions"><span role="status">Google client saved</span><Button ref={replaceRef} variant="secondary" onClick={() => { moveFocus.current = true; setReplacing(true); }}>Replace</Button></div>;
  return <form className="form-stack" aria-label="Set up Google" onSubmit={event => void save(event)}>
    <h3>Set up Google</h3>
    <p>OpenDot is open source, so you use your own Google client. Your data goes straight from Google to this computer.</p>
    <ol className="connection-steps">{googleSteps.map(([label, url]) => <li key={url}><a href={url} target="_blank" rel="noopener noreferrer">{label}</a></li>)}</ol>
    <small className="muted">Google will warn the app is unverified; choose Advanced, then Go to OpenDot.</small>
    <Input ref={uploadRef} label="Upload the JSON file Google gave you" type="file" accept=".json,application/json" disabled={mutation.pending || reading} hint="JSON only, up to 64 KB." onChange={event => {
      const file = event.target.files?.[0];
      event.target.value = "";
      if (file) void upload(file);
    }} />
    {reading && <p role="status">Reading the JSON file…</p>}
    {uploadError && <p role="alert">{uploadError}</p>}
    {isWeb && <p role="alert">This is a Web application client. The Google client type should be Desktop app.</p>}
    {uploaded && <p role="status">Client details ready. Save to continue.</p>}
    <details className="connection-disclosure"><summary>Paste the ID and secret instead</summary><div className="form-stack">
      <Input label="Google client ID" value={clientId} onChange={event => setClientId(event.target.value)} autoComplete="off" spellCheck={false} disabled={mutation.pending || reading} />
      <Input label="Google client secret" type="password" value={secret} onChange={event => setSecret(event.target.value)} autoComplete="new-password" spellCheck={false} disabled={mutation.pending || reading} hint="Write-only. OpenDot will not display the secret after saving it." />
    </div></details>
    {mutation.error != null && <ErrorState error={mutation.error} />}
    {mutation.notice && <p role="status">{mutation.notice}</p>}
    <div className="actions"><Button type="submit" loading={mutation.pending} disabled={reading || !clientId.trim() || !secret.trim()}>Save Google client</Button></div>
  </form>;
}

export function GithubSetup({ onRefresh }: { onRefresh?: () => void }) {
  const [token, setToken] = useState("");
  const mutation = useMutation();
  async function save(event: FormEvent) {
    event.preventDefault();
    const body = { token: token.trim() };
    setToken("");
    const result = await mutation.run(() => api.call("github_token_set", { body }));
    if (result) {
      mutation.setNotice(result.configured ? "GitHub token saved." : "The GitHub token was not saved. Try again.");
      onRefresh?.();
    }
  }
  async function remove() {
    setToken("");
    const result = await mutation.run(() => api.call("github_token_remove"));
    if (result) {
      mutation.setNotice(result.configured ? "The GitHub token is still configured. Try removing it again." : "GitHub token removed.");
      onRefresh?.();
    }
  }
  return <form className="form-stack connection-token-form" aria-label="Connect GitHub" onSubmit={event => void save(event)}>
    <h3>Connect GitHub</h3>
    <p>Create a fine-grained personal access token with read access to the repositories you want OpenDot to use. Give it Issues: write only if you want OpenDot to propose issues. Creating an issue still needs your approval in OpenDot.</p>
    <a href="https://github.com/settings/personal-access-tokens/new" target="_blank" rel="noopener noreferrer">Create a fine-grained GitHub token</a>
    <Input label="GitHub personal access token" type="password" value={token} onChange={event => setToken(event.target.value)} required autoComplete="new-password" spellCheck={false} disabled={mutation.pending} hint="Write-only. Paste a token to connect or replace an existing token. Saved tokens are never displayed." />
    {mutation.error != null && <ErrorState error={mutation.error} />}
    {mutation.notice && <p role="status">{mutation.notice}</p>}
    <div className="actions"><Button type="submit" loading={mutation.pending} disabled={!token.trim()}>Save GitHub token</Button><Button variant="secondary" disabled={mutation.pending} onClick={() => void remove()}>Remove GitHub token</Button></div>
  </form>;
}
