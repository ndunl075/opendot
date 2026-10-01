# OpenDot design system

Task 3.2 · 2026-09-30 · Astra

## Principles and identity

Calm, legible, and explicit about permission. Use generous space, plain language,
visible boundaries, and honest empty/error states. Never imply an action happened
or that unavailable usage is zero. Destructive confirmations initially focus Cancel.

Warm paper (`#f6f4ee`), ink (`#252d30`), and petrol blue (`#245c6a`) give the app a
quiet, practical character. Dark mode uses blue charcoal (`#171e21`) with a pale
petrol accent (`#9cd1dc`). Semantic colors are always accompanied by words or icons.
IBM Plex Sans (400/500/600, SIL OFL 1.1) is bundled through Fontsource, with no CDN
or remote font requests. Lucide supplies interface icons under ISC.
The font and Lucide license notices ship in `public/licenses/` and the built UI.

The original **open fold** mark is a broad, angular ribbon with an open interior.
It suggests a page and room for the user's judgment; it has no dots, face, knot,
or brand mascot. `public/app-icon.svg` is square with explicit 1024px dimensions;
`public/favicon.svg` uses the same simple silhouette. The companion avatar is a
related but distinct arrangement of three bent ribbons. Seed hashing and integer
geometry make its SVG deterministic. `createAvatarSeed()` makes a new seed; the
future companion screen should save that seed through the daemon, not localStorage.

## Tokens and themes

Import `design/tokens.css` before `design/components.css`. All color roles are CSS
custom properties, overridden by `[data-theme="dark"]`. Use `--color-text-muted`
for secondary text, `--color-border` for decorative dividers, and
`--color-border-strong` for interactive boundaries. Accent and semantic roles have
matching soft backgrounds; filled buttons use their `on-*` foregrounds.

Spacing is a 4px scale (`--space-1` through `--space-20`); radii run from `sm` to
`xl` plus `pill`. Type tokens cover 12px through responsive 48px, weights and line
heights. Shadows distinguish surfaces and dialogs. Motion tokens are 120/220ms,
with a 1200ms loading cadence; reduced motion disables animation and transitions.
Contrast tests require 4.5:1 for text pairs and 3:1 for focus/input boundaries.

Wrap the app in `ThemeProvider`. `useTheme()` returns `theme` (resolved light/dark),
`preference` (light/dark/system), and `setTheme(preference)`. System is the default
and follows live OS changes. An explicit override persists as `opendot-theme` in
localStorage; selecting System removes it. Storage failure does not break the UI.
The same-origin `public/theme-init.js` applies the initial preference before paint
without requiring inline scripts under the desktop CSP.

## Components

Import from `src/design/components`. Props extend native React element props where
appropriate, including refs. Use visible labels for every field and `label` for
IconButton. Decorative Lucide icons should have `aria-hidden="true"`.

| Component | Usage |
| --- | --- |
| Button | `variant="primary|secondary|ghost|danger"`, `size="sm|md|lg"`, `loading`; defaults to `type="button"` |
| IconButton | Required `label`; pass a Lucide icon as children |
| Input, Textarea, Select | Required `label`; optional `hint` and `error` are linked with `aria-describedby`; native controlled/uncontrolled props |
| Checkbox, Switch / Toggle | Required `label`; native checkbox behavior, optional `hint`; Switch exposes `role="switch"` |
| Card | Bordered surface; choose semantic headings in its contents |
| Badge | `tone="neutral|accent|success|warning|danger"` |
| UsageStamp | `model`, `effort`, `credits: number | null`; null explicitly means unreported |
| Tabs | Controlled `value`, `onValueChange`, `label`, and `items` with value/label/content/disabled; arrows, Home and End select tabs |
| Dialog | Controlled `open`, `onClose`, `title`, optional `description`, `initialFocusRef`; native modal top layer, focus wrapping, Escape and focus restoration |
| ConfirmDialog | Dialog props plus `confirmLabel`, `onConfirm`, `danger`, `loading`; parent handles successful closure and errors |
| Toast | `message`, `onDismiss`, optional `tone` and `duration`; persistent by default, timed dismissal pauses on hover/focus; mount in your notification region |
| Tooltip | `content` and one focusable button child (Button/IconButton supported); hover/focus, Escape dismissal; do not put essential instructions only in a tooltip |
| Skeleton | `label` for the loading announcement; size with className; no animation with reduced motion |
| EmptyState | `title`, `description`, optional decorative `icon` and `action` |
| ErrorState | `error`, optional `onRetry`; `ApiError.code === "not_implemented"` explains version availability and omits retry |

```tsx
const [seed, setSeed] = useState("first-companion");
<Avatar seed={seed} label="Your companion's avatar" />
<Button variant="secondary" onClick={() => setSeed(createAvatarSeed())}>Re-roll</Button>

<Input label="Companion name" value={name} onChange={e => setName(e.target.value)} />
<UsageStamp model={reply.model} effort={reply.effort} credits={reply.credits ?? null} />
```

The React Router shell exposes all nine v0.1 screens and About. Except About,
screens are explicit placeholders for task 3.3. Mobile navigation is a keyboard
operable disclosure; route changes focus the main content. A skip link and visible
focus rings are provided. The footer reports only the actual health request result.
The browser-only fixture at `/e2e/fixtures/components.html` exercises primitives;
it is not a production route and is excluded from the production build.

## Daemon connection

The desktop shell injects `window.__OPENDOT__ = { token, baseUrl }` before the app
loads. Token precedence is desktop injection, web meta `opendot-api-token`, then
development-only `VITE_OPENDOT_TOKEN`. Base URL precedence is desktop injection,
development-only `VITE_OPENDOT_BASE_URL`, then same origin. Use an HTTP(S) URL
without embedded credentials, query, or fragment. Trailing slashes are normalized;
optional path prefixes are preserved for both transports.

HTTP uses Authorization bearer headers. WebSocket uses the same base URL with
`ws:`/`wss:` and the existing bearer subprotocol. Tokens never enter URLs or
localStorage. The daemon/desktop integration must permit the desktop origin and
the loopback connection in CORS/CSP; this task only configures the UI client.

## Verification and scope

Run from the repository root: `pnpm -C ui lint`, `pnpm -C ui test`,
`pnpm -C ui build`, `pnpm -C ui e2e`. Scripts and Playwright web servers invoke
tools directly, never nested pnpm/npm/corepack. Unit coverage includes palette
contrast, avatar determinism, themes, API base/token precedence, dialogs, tabs,
tooltips, errors and the exact About copy. Playwright exercises the real modal,
all routes, mobile keyboard navigation, fonts, and reduced motion against the
contract mock server. No account sign-in or paid model path is involved.

Per task scope, only `ui/` is changed. Project-level STATUS.md and decisions.md
are left to the milestone lead; the dated design choices are recorded here.
Full screens, desktop icon generation/packaging, daemon CORS, and milestone-wide
review remain their respective later tasks.

## Task 3.3 screens (2026-09-30)

The nine screen implementations now live in `src/screens/`. They use the existing
tokens and primitives, with shared layout rules in `src/screens/screens.css`.
Mutations wait for daemon responses; unavailable values never become invented
success or zero usage. ConfirmDialog accepts an optional error to keep failures
inside the accessible modal. OpenAI-required elements remain text until licensed
official assets are supplied. See [TASK-3.3.md](TASK-3.3.md) for verification,
contract gaps, mock limitations, and the milestone handoff.
