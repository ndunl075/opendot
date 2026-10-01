import { useRef, useState } from "react";
import { api } from "../../api/client";
import type { Connection } from "../../api/types.gen";
import { Badge, Button, Card, Checkbox, ConfirmDialog, Dialog, EmptyState, ErrorState, Skeleton, Switch } from "../../design/components";
import { ScreenHeading, useMutation, useResource } from "../shared";
import { appLabels, ConnectionPicker } from "./ConnectionPicker";

const loadConnections = () => api.call("connections_list");
const healthLabels: Record<Connection["health"], string> = { ok: "OK", stale: "Stale", error: "Error", never_synced: "Never synced" };
function writeLabel(connection: Connection) { return connection.app === "gmail" ? "Allow Gmail drafts" : "Allow Calendar events"; }

export default function ConnectionsScreen() {
  const resource = useResource(loadConnections);
  const mutation = useMutation();
  const [disconnecting, setDisconnecting] = useState<Connection>();
  const [forget, setForget] = useState(false);
  const [write, setWrite] = useState<Connection>();
  const cancelRef = useRef<HTMLButtonElement>(null);
  function replace(connection: Connection) { resource.setData(previous => previous ? { connections: previous.connections.map(item => item.id === connection.id ? connection : item) } : previous); }
  async function sync(connection: Connection) {
    const result = await mutation.run(() => api.call("connection_sync", { params: { connection_id: connection.id } }));
    if (result) { replace(result); mutation.setNotice(`${appLabels[connection.app]} sync requested.`); }
  }
  async function writeAccess(connection: Connection, enabled: boolean) {
    const result = await mutation.run(() => api.call("connection_write_opt_in", { params: { connection_id: connection.id }, body: { enabled } }));
    if (result) { replace(result); setWrite(undefined); mutation.setNotice(result.write_opt_in ? "Write access is opted in. Creating drafts or events still requires approval. Reconnect if the account needs new Google permissions." : enabled ? "Write access is still off. Reconnect the account if Google needs you to approve new permissions." : "Write access is off."); }
    else setWrite(undefined);
  }
  async function disconnect() {
    if (!disconnecting) return;
    const connection = disconnecting;
    const result = await mutation.run(() => api.call("connection_disconnect", { params: { connection_id: connection.id }, body: { forget_learned: forget } }));
    if (result?.disconnected) {
      resource.setData(previous => previous ? { connections: previous.connections.filter(item => item.id !== connection.id) } : previous);
      setDisconnecting(undefined); mutation.setNotice(`${appLabels[connection.app]} disconnected.${forget ? result.forgotten_count === undefined ? " Forgetting requested." : ` ${result.forgotten_count} memories forgotten.` : " Learned memories were kept."}`);
    } else if (result) mutation.setNotice("The account was not disconnected. Try again.");
  }
  return <section className="screen-stack"><ScreenHeading title="Connections" description="Give your companion useful context, one account at a time." />
    <Card className="section-card form-stack"><h2>Connect an app</h2><ConnectionPicker onRefresh={() => void resource.reload()} /></Card>
    {mutation.error != null && !disconnecting && <ErrorState error={mutation.error} />}
    {mutation.notice && <p className="notice" role="status">{mutation.notice}</p>}
    {resource.loading ? <Skeleton className="screen-skeleton" label="Loading connections" /> : resource.error ? <ErrorState error={resource.error} onRetry={resource.reload} /> : resource.data?.connections.length ? <div className="screen-grid">{resource.data.connections.map(connection => <Card key={connection.id} className="section-card form-stack">
      <div className="section-heading"><h2>{appLabels[connection.app]}</h2><Badge tone={connection.health === "ok" ? "success" : connection.health === "error" ? "danger" : "warning"}>{healthLabels[connection.health]}</Badge></div>
      <p>{connection.account_label}</p>{connection.health_detail && <p className="muted">{connection.health_detail}</p>}
      <p className="muted">{connection.last_synced_at ? <>Last synced <time dateTime={connection.last_synced_at}>{new Date(connection.last_synced_at).toLocaleString()}</time></> : "No sync recorded yet"}</p>
      <Badge>{connection.write_opt_in ? "Write access opted in" : "Read-only"}</Badge>
      {connection.app !== "github" && connection.write_opt_in_available && <Switch label={writeLabel(connection)} checked={connection.write_opt_in ?? false} disabled={mutation.pending} hint="Off by default. Requires Google write access; each draft or event still needs approval." onChange={event => { if (event.target.checked) setWrite(connection); else void writeAccess(connection, false); }} />}
      <div className="actions"><Button variant="secondary" disabled={mutation.pending} aria-label={`Sync ${appLabels[connection.app]} now`} onClick={() => void sync(connection)}>Sync now</Button><Button variant="ghost" disabled={mutation.pending} aria-label={`Disconnect ${appLabels[connection.app]}`} onClick={() => { setForget(false); setDisconnecting(connection); }}>Disconnect</Button></div>
    </Card>)}</div> : <EmptyState title="No accounts connected" description="Your companion can work with what you share in chat. Connect an account above whenever you are ready." />}
    <Dialog open={Boolean(disconnecting)} title={`Disconnect ${disconnecting ? appLabels[disconnecting.app] : "account"}?`} onClose={() => { if (!mutation.pending) setDisconnecting(undefined); }} description="Sync will stop and the companion will lose access to this account." initialFocusRef={cancelRef}>
      <div className="form-stack"><Checkbox label="Forget everything learned from this account" checked={forget} onChange={event => setForget(event.target.checked)} hint="Forgetting removes learned memories and cannot be undone." />
        {mutation.error != null && <ErrorState error={mutation.error} />}
        <div className="actions"><Button ref={cancelRef} variant="secondary" disabled={mutation.pending} onClick={() => setDisconnecting(undefined)}>Cancel</Button><Button variant="danger" loading={mutation.pending} onClick={() => void disconnect()}>Disconnect account</Button></div>
      </div>
    </Dialog>
    <ConfirmDialog open={Boolean(write)} title={`${write ? writeLabel(write) : "Allow write access"}?`} description="This opts into Google write access to create Gmail drafts or Calendar events. Every proposed draft or event still needs your approval. OpenDot cannot send email in this version." confirmLabel="Allow write access" loading={mutation.pending} onClose={() => { if (!mutation.pending) setWrite(undefined); }} onConfirm={() => { if (write) void writeAccess(write, true); }} />
  </section>;
}
