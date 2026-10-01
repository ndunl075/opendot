import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

export type Theme = "light" | "dark";
export type ThemePreference = Theme | "system";
const storageKey = "opendot-theme";
const validPreference = (value: string | null): ThemePreference => value === "light" || value === "dark" ? value : "system";
function savedPreference(): ThemePreference {
  try { return validPreference(localStorage.getItem(storageKey)); } catch { return "system"; }
}

const ThemeContext = createContext<{
  preference: ThemePreference;
  theme: Theme;
  setTheme: (preference: ThemePreference) => void;
} | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [preference, setPreference] = useState(savedPreference);
  const [media] = useState(() => window.matchMedia("(prefers-color-scheme: dark)"));
  const [systemDark, setSystemDark] = useState(media.matches);
  const theme = preference === "system" ? (systemDark ? "dark" : "light") : preference;

  useEffect(() => {
    const change = () => setSystemDark(media.matches);
    media.addEventListener("change", change);
    const sync = (event: StorageEvent) => {
      if (event.key === storageKey || event.key === null) setPreference(savedPreference());
    };
    window.addEventListener("storage", sync);
    return () => { media.removeEventListener("change", change); window.removeEventListener("storage", sync); };
  }, [media]);

  useEffect(() => { document.documentElement.dataset.theme = theme; }, [theme]);

  function setTheme(next: ThemePreference) {
    setPreference(next);
    try {
      if (next === "system") localStorage.removeItem(storageKey);
      else localStorage.setItem(storageKey, next);
    } catch { /* Storage may be unavailable in a restricted webview; keep this session usable. */ }
  }

  return <ThemeContext.Provider value={{ preference, theme, setTheme }}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  const context = useContext(ThemeContext);
  if (!context) throw new Error("useTheme must be used inside ThemeProvider");
  return context;
}
