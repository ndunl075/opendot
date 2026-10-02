# OpenDot design system

Task 3.2 · 2026-09-30 · Astra. Restyle updated 2026-10-01.

## Principles and identity

Calm, legible, and explicit about permission. Use generous space, plain language,
visible boundaries, and honest empty/error states. Never imply an action happened
or that unavailable usage is zero. Destructive confirmations initially focus Cancel.

Cool gray (`#eef0f3`), white surfaces, ink (`#16181d`), and OpenDot teal (`#167676`)
give the app a light, quiet character. Dark mode uses charcoal (`#111419`), raised
surfaces (`#191d23`), and a pale teal link accent (`#86d2c9`). User bubbles retain
the deeper teal with white text in both themes (5.40:1 contrast). Semantic colors
are always accompanied by words or icons. Inter Variable (SIL OFL 1.1) is bundled
through Fontsource, with no CDN or remote font requests. The mono stack is kept.
Lucide supplies interface icons under ISC.
The font and Lucide license notices ship in `public/licenses/` and the built UI.

The original **open fold** mark is a broad, angular ribbon with an open interior.
It suggests a page and room for the user's judgment; it has no dots, face, knot,
or brand mascot. `public/app-icon.svg` is square with explicit 1024px dimensions;
`public/favicon.svg` uses the same simple silhouette. Legacy companion seeds use
a related arrangement of three bent ribbons, generated deterministically.
New picker choices use a solid color circle with optional original clay characters
and pixel pets. Both render through `Avatar`; the daemon stores their seed.

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

The React Router shell exposes all nine v0.1 screens and About. A 64px icon rail
leaves room for a rounded main surface. The OD initials at its foot represent the
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

- Common chat patterns inform the layout. OpenDot keeps its own angular mark,
  deterministic ribbon artwork, Inter typography, teal, and original copy.
- Application color roles and legacy avatar palettes live in
  `src/design/tokens.css`; the new artwork palette lives in `design/characters/`.
  Near-black primary pills, soft secondary pills, and
  quiet cards apply across every screen. Dark mode retains the same hierarchy.
- Chat has a centered column, rounded assistant bubbles, solid teal user bubbles,
  and a floating pill composer. The plus starts a new conversation and is disabled
  while a draft or response is active. There is no attachment or microphone API,
  so no such action is implied. Model, effort, and credit information stays visible.
- History and companion details use labelled disclosure buttons. The chat header
  loads the companion profile; the details card loads connections and tasks while open. No files API
  exists, so no fabricated files section or read receipts are displayed.
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
- The desktop editor is 1200 x 720px with a 60/40 split, 28px corners, 96px artwork,
  32px visual gaps, clipped horizontal rows and a 180px composed preview. A pencil
  opens inline rename; Enter accepts the draft, Escape cancels that name edit.
  On phones, the preview sits beside the name so Save remains visible at 390 x 844.
- `AvatarPicker` is shared with onboarding. Each row is a radiogroup with a single
  tab stop, arrow-key wrapping, Home/End, native Space activation, visible focus,
  and scroll chevrons disabled at the ends. Keyboard selection reveals its tile
  without scrolling the dialog. Reduced motion disables smooth scrolling.
- Twelve original solid swatches, ten SVG clay characters and eight 16 x 16 pixel
  pets live in `src/design/characters/`. Clay uses layered radial materials,
  rim lighting, glossy eyes, shaded accessories and soft contact shadows. Pets
  use crisp rects, dark outlines, highlights and shaded edges. No raster assets.
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

Run from `ui/`, all exit 0:

| Command | Result |
| --- | --- |
| `node ./node_modules/eslint/bin/eslint.js .` | Pass, no lint findings |
| `node ./node_modules/typescript/bin/tsc --noEmit` | Pass, no type errors |
| `node ./node_modules/vitest/vitest.mjs run --maxWorkers=2` | 29 files, 248 tests passed |
| `node scripts/check-types.mjs && node ./node_modules/vite/bin/vite.js build` | Generated types match; production build passed, 1,957 modules |
| `node ./node_modules/@playwright/test/cli.js test --config .visual-check/playwright.config.ts --workers=2` | 20 tests passed against the contract mock on isolated ports |

Browser coverage includes desktop, all nine screens at 720px and 390px in both
themes, 320px keyboard navigation, native modal focus, local font loading,
approval flows, and Google connection setup. Screenshots are retained in the
ignored `test-results/` directory. The restyle does not constitute live-account
or daemon release validation. All tracked changes are confined to `ui/`; daemon,
contract, and generated API files have no diff. No commit was made.

The standard Playwright suite also ran, but another checkout restarted its mock
server on the shared 8787 port during the final rerun. The ignored local config
uses ports 5184/8798 for the exact same full suite and leaves that server alone.
Font assertions verify the actual page origin, so they retain the same-origin
requirement regardless of the local test port.
