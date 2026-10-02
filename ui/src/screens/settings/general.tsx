import { useState } from "react";
import { api } from "../../api/client";
import type { Settings, SettingsUpdateRequest } from "../../api/types.gen";
import { Button, Card, ConfirmDialog, ErrorState, Input, Select, Switch } from "../../design/components";
import { useMutation } from "../shared";

export function GeneralSettings({ settings, onSaved }: { settings: Settings; onSaved: (settings: Settings) => void }) {
  const [awake, setAwake] = useState(settings.keep_awake);
  const [quiet, setQuiet] = useState(settings.quiet_hours);
  const [style, setStyle] = useState(settings.style_preset);
  const [overrides, setOverrides] = useState(settings.tier_overrides);
  const [confirmTop, setConfirmTop] = useState(false);
  const mutation = useMutation();
  async function save(body: SettingsUpdateRequest) {
    const result = await mutation.run(() => api.call("settings_update", { body }));
    if (result) { onSaved(result); mutation.setNotice("Settings saved."); setConfirmTop(false); }
  }
  return <>
    <form className="screen-stack" onSubmit={event => { event.preventDefault(); void save({ keep_awake: awake, quiet_hours: quiet, style_preset: style, tier_overrides: overrides }); }}>
      <Card className="section-card form-stack"><h2>Availability</h2>
        <Switch label="Keep awake" checked={awake.enabled} onChange={event => setAwake({ ...awake, enabled: event.target.checked })} hint="Closing a laptop lid usually sleeps it anyway. Keep-awake drains the battery; it does not add model calls." />
        <Switch label="Only while plugged in" checked={awake.only_while_plugged_in ?? true} onChange={event => setAwake({ ...awake, only_while_plugged_in: event.target.checked })} />
        <Switch label="Quiet hours" checked={quiet.enabled} onChange={event => setQuiet({ ...quiet, enabled: event.target.checked })} />
        <div className="screen-grid"><Input type="time" label="Quiet hours start" required value={quiet.start} onChange={event => setQuiet({ ...quiet, start: event.target.value })} /><Input type="time" label="Quiet hours end" required value={quiet.end} onChange={event => setQuiet({ ...quiet, end: event.target.value })} /><Input label="Quiet hours timezone" required value={quiet.timezone} onChange={event => setQuiet({ ...quiet, timezone: event.target.value })} /></div>
      </Card>
      <Card className="section-card form-stack"><h2>Model choices</h2><p className="muted">Use cheap models by default. Overrides choose a tier for a job; the daemon discovers the models available in your account.</p>
        <Switch label="Automatic top-tier use" checked={settings.auto_top_tier ?? false} disabled={mutation.pending} onChange={event => { if (event.target.checked) setConfirmTop(true); else void save({ auto_top_tier: false }); }} hint="Top-tier work uses substantially more plan credits. Otherwise, your companion asks first." />
        {!overrides.length && <p className="muted">No overrides. Default routing is in use.</p>}
        {overrides.map((override, index) => <fieldset className="form-stack" key={index}><legend>Override {index + 1}</legend><div className="screen-grid">
          <Input label={`Job type ${index + 1}`} required value={override.job_type} onChange={event => setOverrides(overrides.map((item, i) => i === index ? { ...item, job_type: event.target.value } : item))} />
          <Select label={`Model tier ${index + 1}`} value={override.tier} onChange={value => setOverrides(overrides.map((item, i) => i === index ? { ...item, tier: value as typeof override.tier } : item))} options={[{ value: "luna", label: "Cheap" }, { value: "terra", label: "Mid" }, { value: "sol", label: "Top" }]} />
          <Select label={`Effort ${index + 1}`} value={override.effort ?? ""} onChange={value => setOverrides(overrides.map((item, i) => i === index ? { ...item, effort: value as typeof override.effort || null } : item))} options={[{ value: "", label: "Default" }, { value: "low", label: "Low" }, { value: "medium", label: "Medium" }, { value: "high", label: "High" }]} />
        </div><Button variant="ghost" onClick={() => setOverrides(overrides.filter((_, i) => i !== index))}>Remove override {index + 1}</Button></fieldset>)}
        <div className="actions"><Button variant="secondary" onClick={() => setOverrides([...overrides, { job_type: "", tier: "luna", effort: "low" }])}>Add model override</Button></div>
      </Card>
      <Card className="section-card form-stack"><h2>Conversation style</h2><Select label="Style preset" value={style} onChange={value => setStyle(value as Settings["style_preset"])} options={[{ value: "concise", label: "Concise" }, { value: "warm", label: "Warm" }, { value: "formal", label: "Formal" }, { value: "playful", label: "Playful" }]} /><div className="actions"><Button type="submit" loading={mutation.pending}>Save settings</Button></div></Card>
    </form>
    {!!mutation.error && !confirmTop && <ErrorState error={mutation.error} />}{mutation.notice && <p role="status">{mutation.notice}</p>}
    <ConfirmDialog open={confirmTop} error={mutation.error} onClose={() => { if (!mutation.pending) setConfirmTop(false); }} title="Allow automatic top-tier use?" description="Top-tier models use substantially more of your plan. Your companion will be allowed to escalate without asking first, within your budgets." confirmLabel="Allow top-tier use" loading={mutation.pending} onConfirm={() => { void save({ auto_top_tier: true }); }} />
  </>;
}
