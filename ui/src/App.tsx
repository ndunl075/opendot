import { useEffect, useRef, useState } from "react";
import { Activity, BookOpen, Brain, Cable, ChevronRight, Gauge, Info, LogOut, Menu, MessageCircle, Plug, Settings, ShieldCheck, Smile, Sprout, X, type LucideIcon } from "lucide-react";
import { Link, Navigate, NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { api, ApiError } from "./api/client";
import { isWebApp, signOut } from "./api/connection";
import { Card, EmptyState, ErrorState, IconButton, Select, Skeleton } from "./design/components";
import { useTheme, type ThemePreference } from "./design/theme";
import { useResource } from "./screens/shared";
import OnboardingScreen from "./screens/onboarding/OnboardingScreen";
import ChatScreen from "./screens/chat/ChatScreen";
import CompanionScreen from "./screens/companion/CompanionScreen";
import ActivityScreen from "./screens/activity";
import RulesScreen from "./screens/rules";
import MemoryScreen from "./screens/memory";
import ConnectionsScreen from "./screens/connections/ConnectionsScreen";
import UsageScreen from "./screens/usage/UsageScreen";
import SettingsScreen from "./screens/settings/SettingsScreen";

const loadOnboarding = () => api.call("onboarding_get");

const screens: { path: string; label: string; icon: LucideIcon }[] = [
  { path: "/onboarding", label: "Onboarding", icon: Sprout },
  { path: "/chat", label: "Chat", icon: MessageCircle },
  { path: "/companion", label: "Companion", icon: Smile },
  { path: "/activity", label: "Activity", icon: Activity },
  { path: "/rules", label: "Rules", icon: ShieldCheck },
  { path: "/memory", label: "Memory", icon: Brain },
  { path: "/connections", label: "Connections", icon: Plug },
  { path: "/usage", label: "Usage", icon: Gauge },
  { path: "/settings", label: "Settings", icon: Settings },
];

export const NON_AFFILIATION = "OpenDot is an independent open-source project. It is not affiliated with, endorsed by, or sponsored by OpenAI or Anthropic. ChatGPT is a trademark of OpenAI.";

export function About() {
  return <><PageHeading title="About OpenDot" description="An independent project. A personal companion." />
    <Card className="about-card"><img src="/app-icon.svg" alt="" width="72" height="72" />
      <p className="eyebrow">Made for your own corner of the world</p>
      <h2>Useful company.<br />Room for your judgment.</h2>
      <p>OpenDot is a personal companion designed to run on your own computer. It remembers context, helps you keep track of things, and asks before acting.</p>
      <div className="about-principles"><span><ShieldCheck size={18} aria-hidden="true" /> Your permission matters</span><span><BookOpen size={18} aria-hidden="true" /> Open source, Apache-2.0</span></div>
      <p className="non-affiliation">{NON_AFFILIATION}</p>
    </Card></>;
}

function PageHeading({ title, description }: { title: string; description: string }) {
  return <header className="page-heading"><p className="eyebrow">Your personal companion</p><h1>{title}</h1><p>{description}</p></header>;
}

function ConnectionStatus() {
  const [status, setStatus] = useState("Checking daemon");
  useEffect(() => {
    let active = true;
    let generation = 0;
    const check = () => {
      const request = ++generation;
      api.call("health").then(health => { if (active && request === generation) setStatus(health.status === "ok" ? "Daemon connected" : "Daemon needs attention"); })
        .catch(error => { if (active && request === generation) setStatus(error instanceof ApiError && error.status === 401 ? "Daemon unauthorized — your session expired" : "Daemon unreachable"); });
    };
    check();
    const timer = window.setInterval(check, 30000);
    window.addEventListener("focus", check);
    return () => { active = false; clearInterval(timer); window.removeEventListener("focus", check); };
  }, []);
  if (status === "Checking daemon" || status === "Daemon connected") return null;
  return <div className="connection-banner" role="alert"><Cable size={18} aria-hidden="true" /><span>{status}. Check the local daemon connection.</span></div>;
}

export function App() {
  const { preference, setTheme } = useTheme();
  const onboarding = useResource(loadOnboarding);
  const navigate = useNavigate();
  const location = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuButton = useRef<HTMLButtonElement>(null);
  const main = useRef<HTMLElement>(null);
  const previousPath = useRef(location.pathname);
  const mustOnboard = !!onboarding.data && onboarding.data.current_step !== "done" && !onboarding.data.completed_steps?.includes("done");
  const gateUnavailable = onboarding.error instanceof ApiError && onboarding.error.code === "not_implemented";
  const current = screens.find(screen => screen.path === location.pathname)?.label ?? (location.pathname === "/" ? "Chat" : location.pathname === "/about" ? "About" : "Page not found");
  useEffect(() => {
    document.title = `${current} · OpenDot`;
    if (previousPath.current !== location.pathname) { main.current?.focus(); previousPath.current = location.pathname; }
  }, [location.pathname, current]);

  return <div className="app-shell">
    <a className="skip-link" href="#main">Skip to content</a>
    <aside className="sidebar">
      <div className="brand-row"><Link to="/" className="brand" aria-label="OpenDot home" onClick={() => setMenuOpen(false)}><img src="/app-icon.svg" width="36" height="36" alt="" /><h2>OpenDot</h2></Link>
        <IconButton className="mobile-menu" label={menuOpen ? "Close navigation" : "Open navigation"} ref={menuButton} aria-expanded={menuOpen} aria-controls="navigation-panel" onClick={() => setMenuOpen(!menuOpen)}>{menuOpen ? <X size={20} /> : <Menu size={20} />}</IconButton></div>
      <div id="navigation-panel" className={`navigation-panel ${menuOpen ? "is-open" : ""}`} onKeyDown={event => { if (event.key === "Escape" && menuOpen) { setMenuOpen(false); menuButton.current?.focus(); } }}>
        <nav aria-label="Main navigation">{screens.map(({ path, label, icon: Icon }) => <NavLink key={path} to={path} title={label} aria-label={label} className={({ isActive }) => `nav-item ${isActive || (path === "/chat" && location.pathname === "/") ? "is-active" : ""}`} onClick={() => setMenuOpen(false)}>
          <Icon size={19} strokeWidth={1.6} aria-hidden="true" /><span>{label}</span>
        </NavLink>)}</nav>
        <div className="sidebar-bottom">
          <NavLink to="/about" title="About" aria-label="About" className={({ isActive }) => `nav-item ${isActive ? "is-active" : ""}`} onClick={() => setMenuOpen(false)}><Info size={19} strokeWidth={1.6} aria-hidden="true" /><span>About</span></NavLink>
          {isWebApp() && <button type="button" title="Sign out" aria-label="Sign out" className="nav-item" onClick={() => signOut()}><LogOut size={19} strokeWidth={1.6} aria-hidden="true" /><span>Sign out</span></button>}
          <Link to="/settings" className="workspace-avatar" aria-label="Local workspace settings" title="Local workspace settings" onClick={() => setMenuOpen(false)}>OD</Link>
        </div>
      </div>
    </aside>
    <div className={`workspace ${current === "Chat" ? "workspace--chat" : ""}`}><div className="topbar"><div className="breadcrumb"><span>OpenDot</span><ChevronRight size={14} aria-hidden="true" /><span>{current}</span></div>
      <Select label="Appearance" value={preference} onChange={value => setTheme(value as ThemePreference)} options={[{ value: "system", label: "System" }, { value: "light", label: "Light" }, { value: "dark", label: "Dark" }]} />
    </div>
      <ConnectionStatus />
      <main id="main" ref={main} tabIndex={-1} className="main-content">{location.pathname !== "/about" && location.pathname !== "/onboarding" && onboarding.loading ? <Skeleton className="screen-skeleton" label="Checking setup" /> : location.pathname !== "/about" && location.pathname !== "/onboarding" && onboarding.error && !gateUnavailable ? <ErrorState error={onboarding.error} onRetry={onboarding.reload} /> : mustOnboard && !["/about", "/onboarding"].includes(location.pathname) ? <Navigate to="/onboarding" replace /> : <Routes>
        <Route path="/" element={<Navigate to="/chat" replace />} />
        <Route path="/onboarding" element={<OnboardingScreen onComplete={async () => { await onboarding.reload(); navigate("/chat"); }} />} />
        <Route path="/chat" element={<ChatScreen />} />
        <Route path="/companion" element={<CompanionScreen />} />
        <Route path="/activity" element={<ActivityScreen />} />
        <Route path="/rules" element={<RulesScreen />} />
        <Route path="/memory" element={<MemoryScreen />} />
        <Route path="/connections" element={<ConnectionsScreen />} />
        <Route path="/usage" element={<UsageScreen />} />
        <Route path="/settings" element={<SettingsScreen />} />
        <Route path="/about" element={<About />} />
        <Route path="*" element={<EmptyState title="This page isn’t here" description="Head back to your space to find what you need." icon={<Settings size={26} />} action={<Link to="/chat">Back to Chat</Link>} />} />
      </Routes>}</main>
    </div>
  </div>;
}
