import { useEffect, useState } from "react";
import { api } from "./api/client";

export function App() {
  const [status, setStatus] = useState("Checking daemon health…");
  useEffect(() => {
    api.call("health").then((health) => setStatus(health.status)).catch(() => setStatus("unavailable"));
  }, []);
  return <main><h1>OpenDot</h1><p aria-live="polite">Daemon status: {status}</p></main>;
}
