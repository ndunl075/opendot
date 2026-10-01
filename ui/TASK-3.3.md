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
  sequence resume, inline approve/edit/deny/always-allow, dedicated reviewer
  notes/verdicts, model/effort/credits stamps and all six pause reasons. Sending is
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

1. **Closed.** Pause banners call `task_continue` and `task_approve_top_tier`
   with the paused event's `task_id`, and `plan_limit_resume` for plan limits.
   Each requires an explicit click, explains plan/credit usage, disables while
   pending and after success, and reports the response. Failures never retry
   automatically; missing task IDs disable task actions.
2. **Open, accepted for M4.** There is no model catalog or tier-to-model mapping endpoint. Settings supports
   the contract's job-type-to-tier/effort overrides; account model selection
   remains the daemon's responsibility.
3. **Open, accepted for M4.** Memory search has no item-ID filter, lookup-by-ID endpoint, or cursor. Activity
   links highlight returned memory IDs; if an ID is outside the first 100 search
   results, the UI asks the user to narrow the search. It cannot fetch that item
   directly.
4. **Open, accepted for M4.** Rule create/update requests lack tool/target/sensitivity/cost matching fields;
   update also cannot change action. The UI edits supported fields only.
5. **Closed.** Approval cards use `ApprovalItem.review_note` and `review_verdict`
   separately from the action preview. Activity resolves `approval_id` through
   `approval_get` for the same durable fields. Badges distinguish ok, concern,
   and block; block also has a danger border/background. No preview parsing.
   Activity entries without an approval retain their own `reviewer_note`.
6. **Open, accepted for M4.** The local provider has no server-address configuration endpoint/field, and
   provider spend-cap requests cover paid API providers only. Local enablement
   uses the reported configuration; no invented configuration form or cap.

## Mock and verification boundaries

`node scripts/mock-server.mjs` runs `uv run --project ../daemon opendot mock-server`
from `ui/`. The daemon now selects the `opendot` WebSocket subprotocol itself;
the temporary `scripts/mock_app.py` adapter has been removed.

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

Follow-up verification ran from the repository root using the installed
`%APPDATA%\npm\pnpm.CMD` launcher (pnpm 10.34.6, not on this session's PATH).
Package scripts and Playwright web servers invoke tool binaries or node scripts
directly, never pnpm, npm or corepack.

```text
pnpm -C ui lint                                      exit 0
  eslint . && tsc --noEmit
pnpm -C ui test                                      exit 0
  Test Files  19 passed (19)
       Tests  140 passed (140)
pnpm -C ui build                                     exit 0
  generated-type drift check passed
  1949 modules transformed; built in 3.01s
pnpm -C ui e2e                                       exit 0
  7 passed (18.2s), directly against the daemon mock
git diff --check                                     exit 0
```

The first sandboxed Vitest launch hit Windows `spawn EPERM`; the approved retry
ran successfully. Added tests first exposed the missing pause actions and review
fields. The final suite covers POST URLs and task IDs, pending/duplicate-click
protection, returned results, failures without automatic retry, missing IDs,
new pauses versus late results, all reviewer verdicts, and streamed/durable notes.
No generated API files, contract files, or daemon source files were edited.
All follow-up changes remain uncommitted.

Project-level STATUS.md and docs/decisions.md are left to the milestone lead to
preserve this task's `ui/`-only boundary. Task 3.4's broader end-to-end scenarios,
real account sign-in, and real connector/daemon acceptance remain separate work.
