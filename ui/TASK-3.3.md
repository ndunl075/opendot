# Task 3.3 handoff

2026-09-30 · Astra · branch `m3/ui` · uncommitted, UI-only changes.

All nine v0.1 screens use the committed design components and generated contract
types through `api.call` and `ChatStream`. About retains task 3.2's content.

## Delivered

- Onboarding gates direct routes until the daemon reports completion. Includes
  companion name/avatar choices, ChatGPT start/poll/expiry, a Plus/Pro stop,
  weekly-limit/credits-off acknowledgement, optional read-only connections and
  introduction. Required OpenAI elements are text, with comments for absent
  licensed DevKit assets.
- Chat conversation list, streamed messages/tools, automatic reconnect and
  sequence resume, inline approve/edit/deny/always-allow, complete reviewer
  preview, model/effort/credits stamps and all six pause reasons. Sending is
  disabled until connected; disconnected requests are never resent automatically.
- Companion profile and work tabs, pause/resume, confirmed reset, rename/avatar.
- Activity timeline, evidence links, reviewer disclosures and cursor pagination.
- Rules CRUD, behavior explanations, and locked core denial regardless of a
  fixture's inconsistent behavior label.
- Memory keyword/source/person search, corrections, and confirmed forgetting by
  item/source/time/person.
- Connections health, optional read access, sync, confirmed disconnect with
  optional forgetting, and separately confirmed Google write opt-in.
- Usage totals by task/day/job type, editable task/daily budgets with daily hard
  stop preserved, and Manage usage.
- Settings for keep-awake, plugged-in-only, quiet hours, providers, separate
  feature consent and cost warnings, write-only keys, paid-provider spend caps,
  job-tier overrides, automatic top-tier consent, style, backup and restore.
- Shared loading/error/501 presentation, confirmation errors inside modal focus
  boundaries, safe external URLs, and pessimistic mutations.

## Contract gaps (no endpoints or response fields invented)

1. There is no paused-task continuation or one-time top-tier approval endpoint.
   Stream `resume` only replays events. Pause banners link to existing budget,
   activity, companion or model controls and explicitly explain unavailable
   one-task continuation. A per-task “continue anyway” action cannot be wired.
2. There is no model catalog or tier-to-model mapping endpoint. Settings supports
   the contract's job-type-to-tier/effort overrides; account model selection
   remains the daemon's responsibility.
3. Memory search has no item-ID filter, lookup-by-ID endpoint, or cursor. Activity
   links highlight returned memory IDs; if an ID is outside the first 100 search
   results, the UI asks the user to narrow the search. It cannot fetch that item
   directly.
4. Rule create/update requests lack tool/target/sensitivity/cost matching fields;
   update also cannot change action. The UI edits supported fields only.
5. ApprovalItem has no dedicated durable reviewer-note field. The runtime appends
   it to a streamed approval's `preview`, which the UI renders in full. Reloaded
   approval history cannot recover a note that the daemon omits from GET.
6. The local provider has no server-address configuration endpoint/field, and
   provider spend-cap requests cover paid API providers only. Local enablement
   uses the reported configuration; no invented configuration form or cap.

## Mock and verification boundaries

`node scripts/mock-server.mjs` runs the existing daemon contract mock through
`scripts/mock_app.py`. The adapter only selects the `opendot` WebSocket protocol
the browser requests. The raw daemon mock fails Chromium's protocol negotiation;
the production daemon already selects it. No production authentication changes.

The daemon mock is stateless: mutation responses do not persist changes,
onboarding is always at `connections`, and chat emits a canned reply with reused
message IDs/sequence numbers. Screen unit tests supply stateful typed mock
responses. The browser navigation test marks onboarding complete through a
route fixture, and a separate test verifies the actual unfinished-setup gate
and one real mock WebSocket reply. This is UI/mock evidence, not live sign-in,
connector execution or real account acceptance. Multi-turn fixture persistence
belongs to a future mock/daemon improvement, not an invented UI data store.

Browser checks cover all screens at 720 px in light/dark, no horizontal overflow
or page exceptions, and the existing 320 px keyboard navigation, native dialog,
theme, font and reduced-motion checks. Representative screenshots are in the
ignored Playwright `test-results/` output and were visually inspected.

## Commands and results

`pnpm` is not on this session's PATH. Per the task's fallback, these local
binaries ran with `ui/` as the working directory; package scripts and Playwright
web servers never invoke pnpm, npm or corepack.

```text
node ./node_modules/eslint/bin/eslint.js .             exit 0 (no warnings)
node ./node_modules/typescript/bin/tsc --noEmit         exit 0
node ./node_modules/vitest/vitest.mjs run              exit 0
  Test Files  18 passed (18)
       Tests  121 passed (121)
node scripts/check-types.mjs                          exit 0
node ./node_modules/vite/bin/vite.js build             exit 0
  1948 modules transformed; built in 2.08s
node ./node_modules/@playwright/test/cli.js test        exit 0
  7 passed (11.7s)
git diff --check                                      exit 0
```

The first sandboxed Vitest launch hit Windows `spawn EPERM`; the approved retry
ran successfully. The initial added streaming browser check caught the mock
handshake issue above; the adapter fixed it, and the full suite passed afterward.

Project-level STATUS.md and docs/decisions.md are left to the milestone lead to
preserve this task's `ui/`-only boundary. Task 3.4's broader end-to-end scenarios,
real account sign-in, and real connector/daemon acceptance remain separate work.
