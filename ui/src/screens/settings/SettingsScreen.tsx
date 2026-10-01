import { api } from "../../api/client";
import { Resource, ScreenHeading, useResource } from "../shared";
import { GeneralSettings } from "./general";
import { ProviderSettings } from "./providers";
import { Backups } from "./backups";

const loadSettings = () => api.call("settings_get");
export default function SettingsScreen() {
  const resource = useResource(loadSettings);
  return <div className="screen-stack"><ScreenHeading title="Settings" description="Make room for your companion on your terms." />
    <Resource resource={resource}>{settings => <>
      <GeneralSettings settings={settings} onSaved={resource.setData} />
      <ProviderSettings providers={settings.providers} onChanged={() => { void resource.reload(); }} />
      <Backups />
    </>}</Resource>
  </div>;
}
