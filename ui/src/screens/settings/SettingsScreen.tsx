import { api } from "../../api/client";
import { Resource, ScreenHeading, useResource } from "../shared";
import { AvailabilitySettings, GeneralSettings, ModelSettings } from "./general";
import { ProviderSettings } from "./providers";
import { Backups } from "./backups";

type Section = "general" | "availability" | "models" | "providers" | "backup";

const loadSettings = () => api.call("settings_get");
function SettingsFields({ section }: { section: Exclude<Section, "backup"> }) {
  const resource = useResource(loadSettings);
  return <Resource resource={resource}>{settings => {
    if (section === "providers") return <ProviderSettings providers={settings.providers} onChanged={() => { void resource.reload(); }} />;
    const Form = section === "availability" ? AvailabilitySettings : section === "models" ? ModelSettings : GeneralSettings;
    return <Form settings={settings} onSaved={resource.setData} />;
  }}</Resource>;
}

export default function SettingsScreen({ section = "general" }: { section?: Section }) {
  return <div className="screen-stack"><ScreenHeading title={section[0].toUpperCase() + section.slice(1)} description="Make room for your companion on your terms." />
    {section === "backup" ? <Backups /> : <SettingsFields key={section} section={section} />}
  </div>;
}

export function AvailabilityScreen() { return <SettingsScreen section="availability" />; }
export function ModelsScreen() { return <SettingsScreen section="models" />; }
export function ProvidersScreen() { return <SettingsScreen section="providers" />; }
export function BackupScreen() { return <SettingsScreen section="backup" />; }
