import { BookOpen, ShieldCheck } from "lucide-react";
import { Card } from "../design/components";
import { ScreenHeading } from "./shared";

export const NON_AFFILIATION = "OpenDot is an independent open-source project. It is not affiliated with, endorsed by, or sponsored by OpenAI or Anthropic. ChatGPT is a trademark of OpenAI.";

export function About() {
  return <><ScreenHeading title="About OpenDot" description="An independent project. A personal companion." />
    <Card className="about-card"><img src="/app-icon.svg" alt="" width="72" height="72" />
      <p className="eyebrow">Made for your own corner of the world</p>
      <h2>Useful company.<br />Room for your judgment.</h2>
      <p>OpenDot is a personal companion designed to run on your own computer. It remembers context, helps you keep track of things, and asks before acting.</p>
      <div className="about-principles"><span><ShieldCheck size={18} aria-hidden="true" /> Your permission matters</span><span><BookOpen size={18} aria-hidden="true" /> Open source, Apache-2.0</span></div>
      <p className="non-affiliation">{NON_AFFILIATION}</p>
    </Card></>;
}

