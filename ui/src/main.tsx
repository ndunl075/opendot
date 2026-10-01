import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { BrowserRouter } from "react-router-dom";
import { ThemeProvider } from "./design/theme";
import "@fontsource/ibm-plex-sans/latin-400.css";
import "@fontsource/ibm-plex-sans/latin-500.css";
import "@fontsource/ibm-plex-sans/latin-600.css";
import "./design/tokens.css";
import "./design/components.css";
import "./shell.css";

createRoot(document.getElementById("root")!).render(<StrictMode><ThemeProvider><BrowserRouter><App /></BrowserRouter></ThemeProvider></StrictMode>);
