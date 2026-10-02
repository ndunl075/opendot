import { useLayoutEffect, useRef } from "react";
import { Activity, ArrowLeft, Brain, Clock, DatabaseBackup, Gauge, Info, Plug, Settings, ShieldCheck, SlidersHorizontal, Smile, Waypoints } from "lucide-react";
import { Link, useLocation, useParams } from "react-router-dom";
import { EmptyState } from "../../design/components";
import { SettingsPaneContext } from "../shared";
import CompanionScreen from "../companion/CompanionScreen";
import ConnectionsScreen from "../connections/ConnectionsScreen";
import MemoryScreen from "../memory";
import RulesScreen from "../rules";
import UsageScreen from "../usage/UsageScreen";
import ActivityScreen from "../activity";
import SettingsScreen, { AvailabilityScreen, BackupScreen, ModelsScreen, ProvidersScreen } from "./SettingsScreen";
import { About } from "../About";
import "./settings-hub.css";

export const settingsSections = [
  { id: "general", label: "General", icon: Settings, Screen: SettingsScreen },
  { id: "availability", label: "Availability", icon: Clock, Screen: AvailabilityScreen },
  { id: "models", label: "Models", icon: SlidersHorizontal, Screen: ModelsScreen },
  { id: "providers", label: "Providers", icon: Waypoints, Screen: ProvidersScreen },
  { id: "backup", label: "Backup", icon: DatabaseBackup, Screen: BackupScreen },
  { id: "companion", label: "Companion", icon: Smile, Screen: CompanionScreen },
  { id: "connections", label: "Connections", icon: Plug, Screen: ConnectionsScreen },
  { id: "memory", label: "Memory", icon: Brain, Screen: MemoryScreen },
  { id: "rules", label: "Rules", icon: ShieldCheck, Screen: RulesScreen },
  { id: "usage", label: "Usage", icon: Gauge, Screen: UsageScreen },
  { id: "activity", label: "Activity", icon: Activity, Screen: ActivityScreen },
  { id: "about", label: "About", icon: Info, Screen: About },
];

export default function SettingsHub() {
  const { section } = useParams();
  const location = useLocation();
  const heading = useRef<HTMLHeadingElement>(null);
  const listHeading = useRef<HTMLHeadingElement>(null);
  const current = settingsSections.find(item => item.id === (section ?? "general"));
  useLayoutEffect(() => {
    const mobileList = !section && window.matchMedia("(max-width: 800px)").matches;
    (mobileList ? listHeading : heading).current?.focus();
  }, [section, location.pathname]);

  if (!current) return <EmptyState title="This settings section isn’t here" description="Choose a section to continue." action={<Link to="/settings">Back to Settings</Link>} />;
  const { Screen } = current;
  return <div className={`settings-hub ${section ? "settings-hub--detail" : "settings-hub--index"}`}>
    <div className="settings-directory">
      <h1 className="settings-mobile-title" ref={listHeading} tabIndex={-1}>Settings</h1>
      <p className="settings-directory-title" aria-hidden="true">Settings</p>
      <nav aria-label="Settings sections"><ul>{settingsSections.map(({ id, label, icon: Icon }) =>
        <li key={id}><Link to={`/settings/${id}`} aria-current={current.id === id ? "page" : undefined}
          className={`settings-section-link ${current.id === id ? "is-active" : ""}`}>
          <Icon size={18} strokeWidth={1.7} aria-hidden="true" /><span>{label}</span>
        </Link></li>
      )}</ul></nav>
    </div>
    <section className="settings-pane" aria-labelledby="settings-section-heading">
      <Link to="/settings" className="settings-back"><ArrowLeft size={17} aria-hidden="true" />Back to Settings</Link>
      <h1 id="settings-section-heading" ref={heading} tabIndex={-1}>{current.label}</h1>
      <SettingsPaneContext.Provider value={true}><Screen /></SettingsPaneContext.Provider>
    </section>
  </div>;
}
