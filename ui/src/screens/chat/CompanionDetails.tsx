import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Cable, Clock3, ExternalLink, X } from "lucide-react";
import { api } from "../../api/client";
import type { CompanionProfile } from "../../api/types.gen";
import { Avatar } from "../../design/avatar";
import { Button, IconButton } from "../../design/components";
import { Resource, useResource } from "../shared";
import { CompanionEditor } from "../companion/CompanionEditor";
import { appLabels } from "../connections/ConnectionPicker";

const loadProfile = () => api.call("companion_get");
const loadConnections = () => api.call("connections_list");
const loadTasks = () => api.call("companion_tasks");

export function CompanionDetails({ onClose, onUpdated }: { onClose: () => void; onUpdated?: (profile: CompanionProfile) => void }) {
  const profile = useResource(loadProfile);
  const connections = useResource(loadConnections);
  const tasks = useResource(loadTasks);
  const [editing, setEditing] = useState(false);
  const closeButton = useRef<HTMLButtonElement>(null);
  useEffect(() => { closeButton.current?.focus(); }, []);
  return <aside id="companion-details" className="companion-details" aria-label="Companion details" onKeyDown={event => { if (event.key === "Escape" && !editing) { event.stopPropagation(); onClose(); } }}>
    <div className="section-heading details-heading"><span className="eyebrow">Your companion</span><IconButton ref={closeButton} label="Close details" onClick={onClose}><X size={18} /></IconButton></div>
    <Resource resource={profile}>{companion => <>
      <div className="details-profile"><button type="button" className="avatar-edit" aria-label="Customize companion" onClick={() => setEditing(true)}><Avatar seed={companion.avatar_seed} size={76} label={`${companion.name}'s avatar`} /></button><h2>{companion.name}</h2><p className="muted">{companion.paused ? "Paused" : "Ready to help"}</p></div>
      {companion.paused_reason && companion.paused && <p className="muted">{companion.paused_reason}</p>}
      <div className="details-actions"><Button variant="secondary" onClick={() => setEditing(true)}>Customize</Button><Link className="button button--secondary" to="/companion">View profile</Link></div>
      {editing && <CompanionEditor companion={companion} onUpdated={updated => { profile.setData(updated); onUpdated?.(updated); }} onClose={() => setEditing(false)} />}
    </>}</Resource>
    <section className="details-section"><h3>Connections</h3><Resource resource={connections}>{data => data.connections.length ? <ul>{data.connections.slice(0, 3).map(connection => <li key={connection.id}><Cable size={18} aria-hidden="true" /><div><strong>{appLabels[connection.app]}</strong><span>{connection.account_label}</span><span>{connection.health === "ok" ? "Connected" : connection.health.replaceAll("_", " ")}</span></div></li>)}</ul> : <p className="muted">No accounts connected yet.</p>}</Resource><Link to="/connections">Show more<span className="sr-only"> connections</span><ExternalLink size={13} aria-hidden="true" /></Link></section>
    <section className="details-section"><h3>Activity</h3><Resource resource={tasks}>{data => {
      const recent = [...data.in_progress, ...data.scheduled, ...data.completed].slice(0, 4);
      return recent.length ? <ul>{recent.map(task => <li key={task.id}><Clock3 size={18} aria-hidden="true" /><div><strong>{task.title}</strong><span>{task.status.replaceAll("_", " ")}</span></div></li>)}</ul> : <p className="muted">A little breathing room. Your tasks will appear here.</p>;
    }}</Resource><Link to="/activity">Show more<span className="sr-only"> activity</span><ExternalLink size={13} aria-hidden="true" /></Link></section>
  </aside>;
}
