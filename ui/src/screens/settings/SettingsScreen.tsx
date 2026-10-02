import { api } from "../../api/client";
import { Resource, ScreenHeading, useResource } from "../shared";
import { GeneralSettings } from "./general";
import { ProviderSettings } from "./providers";
import { Backups } from "./backups";
import { Select } from "../../design/components";
import { useTheme, type ThemePreference } from "../../design/theme";

const loadSettings = () => api.call("settings_get");
export default function SettingsScreen() {
  const resource = useResource(loadSettings);
  const { preference, setTheme } = useTheme();
  return <div className="screen-stack"><ScreenHeading title="Settings" description="Make room for your companion on your terms." />
    <div className="settings-appearance"><h2>Appearance</h2><Select label="Appearance" value={preference} onChange={value => setTheme(value as ThemePreference)} options={[{ value: "system", label: "System" }, { value: "light", label: "Light" }, { value: "dark", label: "Dark" }]} /></div>
    <Resource resource={resource}>{settings => <>
      <GeneralSettings settings={settings} onSaved={resource.setData} />
      <ProviderSettings providers={settings.providers} onChanged={() => { void resource.reload(); }} />
      <Backups />
    </>}</Resource>
  </div>;
}
