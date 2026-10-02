// Development-only fixture; not imported by the application or included in its build.
import { useState } from "react";
import { createRoot } from "react-dom/client";
import { Badge, Button, Card, Checkbox, ConfirmDialog, Dialog, ErrorState, Input, Select, Skeleton, Switch, Tabs, Textarea, Toast, Tooltip, UsageStamp } from "../../src/design/components";
import { Avatar, createAvatarSeed } from "../../src/design/avatar";
import { ThemeProvider, useTheme } from "../../src/design/theme";
import { ApiError } from "../../src/api/client";
import "@fontsource-variable/inter/wght.css";
import "../../src/design/tokens.css";
import "../../src/design/components.css";

function Fixture() {
  const [open, setOpen] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [tab, setTab] = useState("active");
  const [seed, setSeed] = useState("ribbon");
  const { setTheme } = useTheme();
  return <main style={{ maxWidth: 960, margin: "auto", padding: 32, display: "grid", gap: 24 }}>
    <h1>Design system</h1><div><Button onClick={() => setTheme("light")}>Light</Button> <Button onClick={() => setTheme("dark")}>Dark</Button></div>
    <Card><Avatar seed={seed} /><Button onClick={() => setSeed(createAvatarSeed())}>Re-roll avatar</Button></Card>
    <Card><Button onClick={() => setOpen(true)}>Open dialog</Button> <Button variant="secondary">Secondary</Button> <Button variant="ghost">Ghost</Button> <Button variant="danger" onClick={() => setConfirm(true)}>Forget memory</Button> <Button loading>Saving</Button>
      <Dialog open={open} title="Review action" onClose={() => setOpen(false)} description="Nothing happens without your permission."><Input label="Action name" /><Button disabled>Unavailable</Button><div hidden><Button>Hidden action</Button></div><Button>Last action</Button></Dialog>
      <ConfirmDialog open={confirm} title="Forget memory?" description="This removes the selected memory." confirmLabel="Forget" onConfirm={() => setConfirm(false)} onClose={() => setConfirm(false)} danger />
    </Card>
    <Card><Input label="Companion name" hint="Choose a name that feels right." /><Textarea label="Instructions" /><Select label="Preference" options={[{ value: "Option one", label: "Option one" }, { value: "Option two", label: "Option two" }]} /><Switch label="Keep awake" /><Checkbox label="Ask before acting" /><Input label="Required name" error="Please enter a name." /></Card>
    <Card><Tabs label="Tasks" value={tab} onValueChange={setTab} items={[{ value: "active", label: "In progress", content: "Active tasks" }, { value: "scheduled", label: "Scheduled", content: "Scheduled tasks" }, { value: "done", label: "Completed", content: "Completed tasks" }]} />
      <UsageStamp model="Test model" effort="low" credits={null} /><p><Badge tone="success">Success</Badge> <Badge tone="warning">Warning</Badge> <Badge tone="danger">Error</Badge></p>
    </Card>
    <Tooltip content="Only you can approve"><Button variant="secondary">Approval help</Button></Tooltip>
    <Toast message="Your changes were saved" onDismiss={() => {}} /><Skeleton label="Loading activity" />
    <ErrorState error={new ApiError(501, "not_implemented", "Unavailable")} />
  </main>;
}
createRoot(document.getElementById("root")!).render(<ThemeProvider><Fixture /></ThemeProvider>);
