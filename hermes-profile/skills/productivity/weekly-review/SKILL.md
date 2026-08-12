---
name: weekly-review
description: Pull together the week's open tasks, GitHub notifications, Canvas missing assignments, and remembered context into one retrospective-style summary.
version: 0.1.0
author: Alfred contributors
license: Apache-2.0
metadata:
  hermes:
    tags: [alfred, review, productivity]
    related_skills: [morning-brief, inbox-triage]
---

# Weekly review

## Overview

Unlike the morning brief (today-focused, fully pre-ranked) or inbox triage
(one connector), a weekly review pulls from several sources and needs you to
find the throughline yourself -- what actually moved, what's stuck, what's
coming up next week.

## When to use

- The user asks for a weekly review, retrospective, or "how did this week
  go."
- A weekly cron trigger (see `cron/connector-health-check.json` for the
  pattern -- a dedicated weekly-review cron entry is not bundled by default;
  add one only if you want this delivered unprompted).

## Steps

1. Call `brief_get` for the current state of due/overdue tasks and calendar
   items.
2. Call `connector_records_get(connector="github", record_type="notification")`
   for anything GitHub surfaced this week.
3. Call `connector_records_get(connector="canvas", record_type="missing")`
   if the user has Canvas configured, for missing assignments.
4. Call `memory_search` with a query built from the week's obvious themes
   (project names, course names, recurring topics) to surface anything
   remembered that the structured connectors wouldn't otherwise show --
   commitments made in conversation, not tracked as a formal task.
5. Synthesize: what got done (`task_complete` history isn't directly queried
   here today -- infer completion from tasks no longer appearing as open in
   `brief_get`), what's still open and how long it's been open, and what's
   coming due next week.

## Common pitfalls

- Presenting `connector_records_get`'s raw list as the review -- this skill
  exists specifically to add the synthesis step 5 that the other two skills
  deliberately don't attempt.
- Treating a memory returned by `memory_search` as more authoritative than a
  formal task from `brief_get` when they conflict -- flag the conflict to the
  user rather than silently picking one.
- Running this against a Canvas-less or GitHub-less setup and treating the
  resulting empty list as an error -- both connectors are optional; report
  "nothing configured" rather than a failure.

## Verification checklist

- [ ] Every connector call in steps 1-4 that's actually configured was made
      before the summary was written.
- [ ] The summary states a synthesis (what changed, what's stuck), not just
      a concatenation of each tool's raw output.
- [ ] An unconfigured connector (no Canvas/GitHub) was reported as such, not
      silently skipped or treated as an error.
