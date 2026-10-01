# M4 task 4.4: Connections UI

2026-10-01 · Astra · branch `m4/ui-connections` · uncommitted

Connections and onboarding share Google client setup and GitHub token forms.
Google setup renders the daemon's numbered instructions; secrets are masked,
submitted in request bodies, cleared on submission, and never read back.
Gmail and Google Calendar start read-only authorization in the external browser.
The same safe new-tab links work through the desktop shell's existing routing.

Authorization checks run every two seconds, without overlapping requests, for at
most two minutes. Cancel, retry, manual checks, and a fallback browser link are
available. Cancellation and unmount ignore late responses. Cancelling the wait
does not claim to revoke Google access. Write opt-in requires both daemon-reported
write permission and a healthy account before reporting completion; drafts and
events still require approval in OpenDot. Turning write access off makes no
Google authorization request.

GitHub uses the token PUT/DELETE endpoints, with fine-grained repository read
guidance and optional Issues: write. It never uses the OAuth start endpoint.

Design decision: reuse existing form, notice, button, field, and switch primitives;
keep setup inline on both surfaces and retain explicit error and waiting states.
Only `ui/` source and tests are changed. Generated API files are untouched.
Root STATUS.md and docs/decisions.md are left to the milestone lead per task scope.

Verification uses Vitest and Playwright with synthetic credentials and intercepted
stateful endpoints over the contract mock server. Browser tests cover external
tabs with no opener on both surfaces, Gmail and Calendar, secret clearing, and
390px onboarding layout. Real Google/GitHub accounts and desktop shell routing
are not exercised by these UI tests.

Final checks from the repository root (all exit 0):

- `pnpm -C ui lint`: ESLint and TypeScript passed.
- `pnpm -C ui test`: 24 files, 192 tests passed.
- `pnpm -C ui build`: generated-type drift check passed; Vite built 1952 modules.
- `pnpm -C ui e2e`: 14 tests passed, including both new connection flows.
- `git diff --check`: passed. Desktop and 390px setup screenshots inspected.

On this machine pnpm was invoked through `%APPDATA%/npm/pnpm.cmd` because it is
not on PATH. Scripts and Playwright web servers retain direct tool invocations.
The checks needed unsandboxed subprocess execution after esbuild hit spawn EPERM.
