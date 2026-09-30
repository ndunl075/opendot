---
name: morning-brief
description: Render Alfred Core's deterministic morning brief and deliver a short, warm rewrite of it without changing any fact, date, or link it contains.
version: 0.1.0
author: Alfred contributors
license: Apache-2.0
metadata:
  hermes:
    tags: [alfred, briefing, productivity]
    related_skills: [inbox-triage, weekly-review]
---

# Morning brief

## Overview

Alfred Core already does all the gathering, ranking, and formatting for the
morning brief deterministically, with zero model involvement -- overdue
items, items due today, upcoming items, calendar conflicts, and GitHub
notifications are bucketed and sorted before you ever see them. Your only job
here is to fetch that brief and, optionally, make it sound like a person
wrote it -- never to re-derive or re-rank anything yourself.

## When to use

- The user asks for their brief, agenda, or "what's on today."
- A scheduled/cron trigger asks for the daily brief to be delivered
  unprompted.

## Steps

1. Call the `brief_get` MCP tool (optionally with `now` as an ISO-8601
   override, for a brief as of a specific time). This returns the complete,
   already-ranked, already-formatted plain-text brief -- including a
   freshness line and any late-delivery disclosure.
2. If the brief is empty or trivially short (nothing due, nothing overdue),
   say so plainly rather than padding the response.
3. Optionally, rewrite the brief's tone to be short and warm -- but under
   the same hard constraint `BriefingService.write_brief()` uses internally
   for Alfred Core's own optional LLM pass: **preserve every fact, date, and
   link exactly as given. Do not add or invent anything not present in the
   brief.** If you're not confident you can do this without altering a
   fact, deliver the deterministic text from step 1 verbatim instead --
   that's always a safe, correct answer.
4. Deliver the result in the current conversation.

## Common pitfalls

- Re-sorting, re-grouping, or "improving" the ranking `brief_get` already
  did -- Alfred Core owns due-date logic and calendar-conflict detection;
  duplicating it here risks disagreeing with the one system of record.
- Dropping the freshness line or a late-delivery disclosure when rewriting
  for tone -- both are load-bearing information, not boilerplate to trim.
- Calling `agenda_get` instead when a specific `now` override is needed --
  `agenda_get` takes no time argument; use `brief_get` whenever the moment
  matters.

## Verification checklist

- [ ] `brief_get` was called at least once before any brief content was
      stated.
- [ ] Every date, number, and link in the delivered message traces back to
      the raw `brief_get` output.
- [ ] An empty/trivial brief was reported as such, not padded with filler.
