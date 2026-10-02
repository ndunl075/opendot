# OpenDot design system

Task 3.2 · 2026-09-30 · Astra. Restyle updated 2026-10-01.

## Principles and identity

Calm, legible, and explicit about permission. Use generous space, plain language,
visible boundaries, and honest empty/error states. Never imply an action happened
or that unavailable usage is zero. Destructive confirmations initially focus Cancel.

White (`#ffffff`), a near-white rail (`#f9f9f9`), soft surfaces (`#f4f4f4`), ink
(`#0d0d0d`), muted text (`#5d5d5d`) and quiet borders (`#e5e5e5`) define the light theme.
Dark mode uses `#212121`, a `#171717` rail, `#2f2f2f` surfaces and `#ececec` text.
User bubbles use `#2678cc` with white text in both themes (4.52:1 contrast).
The reference blue `#5ea8f2` only reaches 2.51:1 with white; the accessible blue
retains its hue. Links use `#176bb6` in light mode and `#8ac2fa` in dark mode.
Semantic colors
are always accompanied by words or icons. Inter Variable (SIL OFL 1.1) is bundled
through Fontsource, with no CDN or remote font requests. The mono stack is kept.
Lucide supplies interface icons under ISC.
The font and Lucide license notices ship in `public/licenses/` and the built UI.

The original **open fold** mark is a broad, angular ribbon with an open interior.
It suggests a page and room for the user's judgment; it has no dots, face, knot,
or brand mascot. `public/app-icon.svg` is square with explicit 1024px dimensions;
`public/favicon.svg` uses the same simple silhouette. Legacy companion seeds use
a related arrangement of three bent ribbons, generated deterministically.
New picker choices use a shaded ring with a white center 30% of its diameter.
Choosing a character replaces the ring with a soft colored disc beneath the
original clay artwork. Optional original pixel pets sit at the lower right.
Swatches and previews share `AvatarBase`; the daemon stores the unchanged seed format.

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
| Card | Quiet surface; screen sections use a bottom divider; choose semantic headings in its contents |
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

The React Router shell exposes all nine v0.1 screens and About. An 84px labelled rail
sits beside an unframed main surface. The OD initials at its foot represent the
local OpenDot workspace; the API does not expose a user's name. Mobile navigation is a keyboard
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
Desktop icon generation/packaging, daemon CORS, and milestone-wide review are
outside this UI restyle.

## Task 3.3 screens (2026-09-30)

The nine screen implementations now live in `src/screens/`. They use the existing
tokens and primitives, with shared layout rules in `src/screens/screens.css`.
Mutations wait for daemon responses; unavailable values never become invented
success or zero usage. ConfirmDialog accepts an optional error to keep failures
inside the accessible modal. OpenAI-required elements remain text until licensed
official assets are supplied. See [TASK-3.3.md](TASK-3.3.md) for verification,
contract gaps, mock limitations, and the milestone handoff.

## Restyle decisions (2026-10-01)

- The owner's revised direction explicitly permits close matching of ChatGPT's
  general layout, components and colors. OpenDot keeps its own angular mark,
  original character/pet artwork and Inter typography. No OpenAI icons, logos,
  characters or proprietary fonts are used. The mark keeps its geometry in black and white.
- Application color roles and legacy avatar palettes live in
  `src/design/tokens.css`; the new artwork palette lives in `design/characters/`.
  Near-black primary pills, soft secondary pills, and
  quiet section dividers apply across every screen. Inputs have a soft fill and
  blue focus ring; settings switches sit opposite their labels. Dark mode retains the same hierarchy.
- Chat has a centered 1000px column, 26px assistant bubbles, blue user bubbles,
  and a floating pill composer. The plus starts a new conversation and is disabled
  while a draft or response is active. The requested microphone icon is explicitly
  disabled and labelled unavailable because there is no voice API. Unsupported
  phone controls are omitted. Model, effort, and credit information stays visible.
- History and companion details use labelled disclosure buttons. The chat header
  loads the companion profile; the details card loads connections and tasks while open. No files API
  exists, so no fabricated files section is displayed. Only the final user message
  gets a "Read" timestamp, and only once a subsequent assistant response exists.
  This timestamp is the response's start time, not a delivery acknowledgement.
  The transcript scrolls internally; its accessibility labels have containing
  blocks so they cannot introduce page overflow. Resize follows the latest reply
  only while the user is already following the conversation.
- `CompanionEditor` previews name and avatar locally. It uses only the existing
  rename and avatar-seed endpoints, retains successfully saved fields after a
  partial failure, and never saves on cancel. Choices are encoded in `avatar_seed`;
  no unsupported fields are introduced. Options scroll horizontally,
  and the preview stacks below them on phones.
- Native modal focus trapping, Escape, safe initial confirmation focus, route
  focus, accessible nav labels, and reduced-motion support remain in place.
  Tests retain their behavior assertions and add coverage for the new editor,
  rail, history/details controls, contrast pairs, and 390px light/dark layouts.

### Companion picker and labelled rail (2026-10-01)

- The 84px rail shows 11px medium labels below clear Lucide icons, with tooltips,
  explicit accessible names and a soft active background. Mobile navigation keeps
  the existing labelled menu.
- The desktop editor is at most 1200 x 720px with a 60/40 split and 28px corners.
  Height is capped below the viewport; artwork scales from 96px to 64px with
  viewport height, with proportional row spacing. Only the options pane scrolls
  vertically if needed. The preview and Save stay fixed. A quiet 32px pencil
  sits 12px from the name, aligned with its first line, and
  opens inline rename; Enter accepts the draft, Escape cancels that name edit.
  On phones, the preview sits beside the name so Save remains visible at 390 x 844.
- `AvatarPicker` is shared with onboarding. Each row is a radiogroup with a single
  tab stop, arrow-key wrapping, Home/End, native Space activation, visible focus,
  and scroll chevrons disabled at the ends. Keyboard selection reveals its tile
  without scrolling the dialog. Reduced motion disables smooth scrolling.
- Twelve ring swatches, ten original SVG clay characters and eight 16 x 16 pixel
  pets live in `src/design/characters/`. Clay uses layered radial materials,
  rim lighting, glossy eyes, shaded accessories and soft contact shadows. Pets
  use crisp rects, dark outlines, highlights and shaded edges. No raster assets.
  Swatch order: slate, sky blue, yellow, orchid, lime, pink, coral, jade, indigo,
  violet, teal and sand. Existing seed IDs and accessible names remain compatible.
- Seeds use `v2:c=<color>;h=<character|none>;p=<pet|none>`. All 1,188 combinations
  round-trip under the 64-character limit. Invalid v2 input falls back to slate;
  every non-v2 seed keeps the woven renderer and is preserved until a choice changes.
  Saved avatars appear in the chat header, empty chat, details, profile and onboarding.
- Rendered and reviewed all artwork at 96px and 180px, plus desktop and mobile
  dialogs in both themes. Contact sheets and desktop captures are in ignored
  `.visual-check/`; phone screenshots are under `test-results/`. The contact-sheet
  fixture is reproducible through `e2e/companion-art.spec.ts` and is outside the build.
- The generated-type drift test now mutates a temporary copy, so an interrupted
  run cannot alter application `*.gen.ts` files. The Google setup focus assertion
  waits for its effect, retaining the same required focus target.

### Restyle verification

Run from `ui/`, all exit 0 (updated for the owner's neutral/ring direction):

| Command | Result |
| --- | --- |
| `node ./node_modules/eslint/bin/eslint.js .` | Pass, no lint findings |
| `node ./node_modules/typescript/bin/tsc --noEmit` | Pass, no type errors |
| `node ./node_modules/vitest/vitest.mjs run` | 29 files, 263 tests passed, including 68 contrast checks |
| `node scripts/check-types.mjs && node ./node_modules/vite/bin/vite.js build` | Generated types match; production build passed, 1,957 modules |
| `node ./node_modules/@playwright/test/cli.js test --config .visual-check/playwright.config.ts --workers=2` | 39 tests passed against the contract mock on isolated ports |

Browser coverage includes the dialog at 1920x1080, 1536x864 and 1280x720 CSS
viewports with 1/1.25/1.5 device scales (the layout equivalents of 100/125/150%
browser zoom on a 1080p display), 1366x768, and 390px phones in both themes.
Geometry checks require every desktop picker row to fit, Save to remain fully
visible, no outer dialog scrolling, 64-96px artwork, and the 12px rename gap.
A 390x600 case exercises internal options scrolling while Save stays fixed.
All nine screens, 320px keyboard navigation, native modal focus, local fonts,
approval flows and Google connection setup are covered. Gallery and responsive
screenshots are retained in the ignored `.visual-check/` directory; other test
captures remain in `test-results/`. The restyle does not constitute live-account
or daemon release validation. All tracked changes are confined to `ui/`; daemon,
contract, and generated API files have no diff. No commit was made.

The ignored local config uses ports 5184/8798 for the exact same full suite,
avoiding the shared preview ports used by the other checkout.
Font assertions verify the actual page origin, so they retain the same-origin
requirement regardless of the local test port.

The onboarding feedback tests previously expected a stateful completion response
from this checkout's stateless contract mock. Their per-page fixture now models
the transition to `intro` after a real successful POST. All pending, disabled,
spinner, error, retry, focus and introduction assertions are retained; the
request method and real response status are also asserted. No daemon or contract
changes were needed.
