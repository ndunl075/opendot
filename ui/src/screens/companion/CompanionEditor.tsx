import { useRef, useState } from "react";
import { Pencil } from "lucide-react";
import { api } from "../../api/client";
import type { CompanionProfile } from "../../api/types.gen";
import { Avatar } from "../../design/avatar";
import { AvatarPicker } from "../../design/AvatarPicker";
import { Button, Dialog, ErrorState, IconButton } from "../../design/components";
import { useMutation } from "../shared";

export function CompanionEditor({ companion, onUpdated, onClose, renameOnOpen = false }: { companion: CompanionProfile; onUpdated: (profile: CompanionProfile) => void; onClose: () => void; renameOnOpen?: boolean }) {
  const [name, setName] = useState(companion.name);
  const [seed, setSeed] = useState(companion.avatar_seed);
  const [editingName, setEditingName] = useState(renameOnOpen);
  const nameInput = useRef<HTMLInputElement>(null);
  const nameBeforeEdit = useRef(name);
  const renameButton = useRef<HTMLButtonElement>(null);
  const saved = useRef(companion);
  const mutation = useMutation();
  async function save() {
    if (!name.trim()) return;
    const result = await mutation.run(async () => {
      if (name.trim() !== saved.current.name) {
        saved.current = await api.call("companion_rename", { body: { name: name.trim() } });
        onUpdated(saved.current);
      }
      if (seed !== saved.current.avatar_seed) {
        saved.current = await api.call("companion_avatar", { body: { avatar_seed: seed } });
        onUpdated(saved.current);
      }
      return true;
    });
    if (result) onClose();
  }
  return <Dialog open title="Customize your companion" className="companion-editor" initialFocusRef={renameOnOpen ? nameInput : undefined} onClose={() => { if (!mutation.pending) onClose(); }}>
    <form className="identity-layout" onSubmit={event => { event.preventDefault(); void save(); }}>
      <div className="identity-options">
        <AvatarPicker seed={seed} onChange={setSeed} disabled={mutation.pending} />
        {mutation.error != null && <ErrorState error={mutation.error} />}
      </div>
      <section className="identity-preview" aria-label="Companion preview">
        <div className="identity-name">
          {editingName ? <input ref={nameInput} aria-label="Companion name" autoFocus required maxLength={40} autoComplete="off" value={name} disabled={mutation.pending}
            onChange={event => setName(event.target.value)} onBlur={() => { if (name.trim()) setEditingName(false); }}
            onKeyDown={event => {
              if (event.key !== "Enter" && event.key !== "Escape") return;
              event.preventDefault(); event.stopPropagation();
              if (event.key === "Escape") setName(nameBeforeEdit.current);
              else if (!name.trim()) return;
              setEditingName(false); renameButton.current?.focus();
            }} /> : <h3>{name.trim() || "Your companion"}</h3>}
          <IconButton ref={renameButton} label="Rename companion" disabled={mutation.pending} onClick={() => { nameBeforeEdit.current = name; setEditingName(true); }}><Pencil size={18} /></IconButton>
        </div>
        <div className="identity-avatar"><Avatar seed={seed} size={180} label="Selected companion avatar" /></div>
        <Button type="submit" size="lg" loading={mutation.pending} disabled={!name.trim()}>Save</Button>
      </section>
    </form>
  </Dialog>;
}
