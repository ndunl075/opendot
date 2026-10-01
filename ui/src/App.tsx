import { useEffect, useRef, useState } from "react";
import { Activity, ArrowUpRight, BookOpen, Cable, ChevronRight, Gauge, Info, Menu, MessageSquare, PanelTop, Settings, ShieldCheck, SlidersHorizontal, Sprout, X, type LucideIcon } from "lucide-react";
import { Link, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { api } from "./api/client";
import { Avatar } from "./design/avatar";
import { Badge, Card, EmptyState, IconButton, Select } from "./design/components";
import { useTheme, type ThemePreference } from "./design/theme";

const screens: { path: string; label: string; icon: LucideIcon; intro: string; empty: string; detail: string }[] = [
  { path: "/onboarding", label: "Onboarding", icon: Sprout, intro: "A thoughtful beginning.", empty: "Make yourself at home", detail: "Companion setup will be available here in an upcoming version." },
  { path: "/chat", label: "Chat", icon: MessageSquare, intro: "A little space to think things through.", empty: "Your conversations will start here", detail: "Chat is being prepared. Soon, you’ll be able to talk things through with your companion and review actions before they happen." },
  { path: "/companion", label: "Companion", icon: PanelTop, intro: "A companion that works on your terms.", empty: "A space for your companion", detail: "Your companion’s profile and work will appear here when this screen is ready." },
  { path: "/activity", label: "Activity", icon: Activity, intro: "Understand what happened, and why.", empty: "The story behind each action", detail: "An activity timeline with the context behind your companion’s work is coming here." },
  { path: "/rules", label: "Rules", icon: ShieldCheck, intro: "You decide where the boundaries are.", empty: "Your rules, clearly written", detail: "You’ll be able to review and manage your companion’s rules here in an upcoming version." },
  { path: "/memory", label: "Memory", icon: BookOpen, intro: "Remember what matters. Let go of what doesn’t.", empty: "A place for what you share", detail: "Memory search, corrections, and controls to forget information are coming here." },
  { path: "/connections", label: "Connections", icon: Cable, intro: "Bring your tools together, with your permission.", empty: "Your connections belong here", detail: "Optional account connections and their health will be available here when this screen is ready." },
  { path: "/usage", label: "Usage", icon: Gauge, intro: "A clear view of what your companion uses.", empty: "Usage you can understand", detail: "Usage details and budget controls are coming here. No usage data is available on this screen yet." },
  { path: "/settings", label: "Settings", icon: SlidersHorizontal, intro: "Make OpenDot fit your day.", empty: "The details, on your terms", detail: "Your companion’s preferences and controls will be available here in an upcoming version." },
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

function Placeholder({ screen }: { screen: typeof screens[number] }) {
  const Icon = screen.icon;
  return <><PageHeading title={screen.label} description={screen.intro} />
    <Card className="placeholder-card"><div className="placeholder-label"><span>{screen.label}</span><Badge>Coming soon</Badge></div>
      <EmptyState title={screen.empty} description={screen.detail} icon={<Icon size={28} strokeWidth={1.5} />} />
      <div className="placeholder-foot"><ShieldCheck size={16} aria-hidden="true" /><span>A little help, with you in control.</span></div>
    </Card>
    <Link to="/about" className="about-link">Get to know OpenDot <ArrowUpRight size={15} aria-hidden="true" /></Link>
  </>;
}

function ConnectionStatus() {
  const [status, setStatus] = useState("Checking daemon");
  useEffect(() => {
    let active = true;
    api.call("health").then(health => { if (active) setStatus(health.status === "ok" ? "Daemon connected" : "Daemon needs attention"); })
      .catch(() => { if (active) setStatus("Daemon unavailable"); });
    return () => { active = false; };
  }, []);
  return <p className="connection-status" role="status"><Cable size={14} aria-hidden="true" />{status}</p>;
}

export function App() {
  const { preference, setTheme } = useTheme();
  const location = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuButton = useRef<HTMLButtonElement>(null);
  const main = useRef<HTMLElement>(null);
  const previousPath = useRef(location.pathname);
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
        <p className="nav-caption">Your space</p>
        <nav aria-label="Main navigation">{screens.map(({ path, label, icon: Icon }) => <NavLink key={path} to={path} className={({ isActive }) => `nav-item ${isActive || (path === "/chat" && location.pathname === "/") ? "is-active" : ""}`} onClick={() => setMenuOpen(false)}>
          <Icon size={19} strokeWidth={1.6} aria-hidden="true" /><span>{label}</span>
        </NavLink>)}</nav>
        <div className="sidebar-bottom"><div className="companion-preview"><Avatar seed="opendot-first-fold" size={42} label="OpenDot companion avatar preview" /><div><strong>A thoughtful companion</strong><span>Asks before it acts</span></div></div>
          <NavLink to="/about" className={({ isActive }) => `nav-item ${isActive ? "is-active" : ""}`} onClick={() => setMenuOpen(false)}><Info size={19} strokeWidth={1.6} aria-hidden="true" /><span>About</span></NavLink>
        </div>
      </div>
    </aside>
    <div className="workspace"><div className="topbar"><div className="breadcrumb"><span>Your space</span><ChevronRight size={14} aria-hidden="true" /><span>{current}</span></div>
      <Select label="Appearance" value={preference} onChange={event => setTheme(event.target.value as ThemePreference)}><option value="system">System</option><option value="light">Light</option><option value="dark">Dark</option></Select>
    </div>
      <main id="main" ref={main} tabIndex={-1} className="main-content"><Routes>
        <Route path="/" element={<Placeholder screen={screens[1]} />} />
        {screens.map(screen => <Route key={screen.path} path={screen.path} element={<Placeholder screen={screen} />} />)}
        <Route path="/about" element={<About />} />
        <Route path="*" element={<EmptyState title="This page isn’t here" description="Head back to your space to find what you need." icon={<Settings size={26} />} action={<Link to="/chat">Back to Chat</Link>} />} />
      </Routes></main>
      <footer className="workspace-footer"><span>OpenDot <span className="footer-divider">/</span> A space of your own</span><ConnectionStatus /></footer>
    </div>
  </div>;
}
