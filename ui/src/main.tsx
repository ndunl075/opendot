import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { BrowserRouter } from "react-router-dom";
import { ThemeProvider } from "./design/theme";
import "@fontsource-variable/inter/wght.css";
import "./design/tokens.css";
import "./design/components.css";
import "./shell.css";
import "./screens/screens.css";

createRoot(document.getElementById("root")!).render(<StrictMode><ThemeProvider><BrowserRouter><App /></BrowserRouter></ThemeProvider></StrictMode>);
