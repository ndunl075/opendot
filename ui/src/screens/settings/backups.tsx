import { useState } from "react";
import { api } from "../../api/client";
import type { BackupInfo } from "../../api/types.gen";
import { Badge, Button, Card, ConfirmDialog, EmptyState, ErrorState } from "../../design/components";
import { Resource, useMutation, useResource } from "../shared";

const loadBackups = () => api.call("backup_status");
export function Backups() {
  const resource = useResource(loadBackups);
  const mutation = useMutation();
  const [restore, setRestore] = useState<BackupInfo>();
  return <Card className="section-card form-stack"><div className="section-heading"><h2>Backup and restore</h2><Button variant="secondary" loading={mutation.pending} onClick={async () => {
    const result = await mutation.run(() => api.call("backup_create", { body: { verify: true } }));
    if (result) { mutation.setNotice(result.verified ? "Backup created and verified." : "Backup created; verification has not passed."); void resource.reload(); }
  }}>Create backup</Button></div>
    <Resource resource={resource}>{data => <><p className="muted">{data.encryption_key_present ? "Backup encryption key is present." : "No backup encryption key is reported. Configure backup encryption in the daemon before creating a backup."}</p>
      {data.backups.length ? <ul className="screen-stack">{data.backups.map(backup => <li key={backup.id}><div className="section-heading"><div><strong>{new Date(backup.created_at).toLocaleString()}</strong><p className="muted">{backup.id} · {backup.size_bytes.toLocaleString()} bytes</p><Badge tone={backup.verified ? "success" : "warning"}>{backup.verified ? "Verified" : "Not verified"}</Badge></div><Button variant="secondary" disabled={mutation.pending} onClick={() => setRestore(backup)} aria-label={`Restore backup ${backup.id}`}>Restore</Button></div></li>)}</ul> : <EmptyState title="No backups yet" description="Create a backup to keep a recoverable copy of your companion's data." />}
    </>}</Resource>
    {!!mutation.error && !restore && <ErrorState error={mutation.error} />}{mutation.notice && <p role="status">{mutation.notice}</p>}
    <ConfirmDialog open={!!restore} error={mutation.error} onClose={() => { if (!mutation.pending) setRestore(undefined); }} title="Restore this backup?" description="Restoring replaces your companion's current data with this backup. Changes made since it was created will be lost." confirmLabel="Restore backup" danger loading={mutation.pending} onConfirm={async () => {
      if (!restore) return;
      const result = await mutation.run(() => api.call("backup_restore", { body: { backup_id: restore.id, confirm: "restore" } }));
      if (result) { setRestore(undefined); mutation.setNotice(result.restored ? `Backup restored.${result.restart_required ? " Restart the daemon to finish restoring." : ""}` : "The daemon did not restore the backup."); void resource.reload(); }
    }} />
  </Card>;
}
