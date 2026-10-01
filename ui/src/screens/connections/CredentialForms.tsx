import { useState, type FormEvent } from "react";
import { api } from "../../api/client";
import type { GoogleClientStatus } from "../../api/types.gen";
import { Button, ErrorState, Input } from "../../design/components";
import { useMutation } from "../shared";

export function GoogleSetup({ status, onSave }: { status: GoogleClientStatus; onSave: (status: GoogleClientStatus) => void }) {
  const [clientId, setClientId] = useState("");
  const [secret, setSecret] = useState("");
  const mutation = useMutation();
  async function save(event: FormEvent) {
    event.preventDefault();
    const body = { client_id: clientId.trim(), client_secret: secret.trim() };
    setSecret("");
    const result = await mutation.run(() => api.call("google_client_set", { body }));
    if (result) {
      onSave(result);
      if (!result.configured) mutation.setNotice("The Google client was not saved. Check the setup steps and try again.");
    }
  }
  return <form className="form-stack" aria-label="Set up Google" onSubmit={event => void save(event)}>
    <h3>Set up Google</h3>
    <p>Bring your own Google OAuth client so OpenDot can connect to your account. Follow these steps once for Gmail and Google Calendar.</p>
    <ol className="connection-steps">{status.setup_steps?.map((step, index) => <li key={index}>{step}</li>)}</ol>
    <a href="https://console.cloud.google.com/" target="_blank" rel="noopener noreferrer">Open Google Cloud Console</a>
    <Input label="Google client ID" value={clientId} onChange={event => setClientId(event.target.value)} required autoComplete="off" spellCheck={false} disabled={mutation.pending} />
    <Input label="Google client secret" type="password" value={secret} onChange={event => setSecret(event.target.value)} required autoComplete="new-password" spellCheck={false} disabled={mutation.pending} hint="Write-only. OpenDot will not display the secret after saving it." />
    {mutation.error != null && <ErrorState error={mutation.error} />}
    {mutation.notice && <p role="status">{mutation.notice}</p>}
    <div className="actions"><Button type="submit" loading={mutation.pending} disabled={!clientId.trim() || !secret.trim()}>Save Google client</Button></div>
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
