// Development-only contact sheets. Vite does not include this in the app build.
import { createRoot } from "react-dom/client";
import { characters, pets } from "../../src/design/characters/catalog";
import { Character } from "../../src/design/characters/Character";
import { PixelPet } from "../../src/design/characters/PixelPet";
import { ThemeProvider } from "../../src/design/theme";
import "@fontsource-variable/inter/wght.css";
import "../../src/design/tokens.css";
import "../../src/design/components.css";

function Artwork() {
  return <main style={{ padding: 32, background: "var(--color-surface)" }}>
    {["characters", "pets"].map(kind => <section key={kind} data-sheet={kind} style={{ padding: 24 }}>
      <h1 style={{ fontSize: 24, marginBottom: 24 }}>{kind === "characters" ? "Original clay companions" : "Original pixel pets"} · 180px / 96px</h1>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 24 }}>
        {(kind === "characters" ? characters : pets).map(item => <article key={item.id} style={{ display: "grid", justifyItems: "center", gap: 8 }}>
          {[180, 96].map(size => <svg key={size} width={size} height={size} viewBox={kind === "characters" ? "0 0 100 100" : "0 0 16 16"} role="img" aria-label={`${item.name} at ${size}px`}>
            {kind === "characters" ? <Character id={item.id as typeof characters[number]["id"]} /> : <PixelPet id={item.id as typeof pets[number]["id"]} />}
          </svg>)}
          <p style={{ fontSize: 14 }}>{item.name}</p>
        </article>)}
      </div>
    </section>)}
  </main>;
}
createRoot(document.getElementById("root")!).render(<ThemeProvider><Artwork /></ThemeProvider>);
