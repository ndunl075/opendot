---
name: inbox-triage
description: Summarize unread Gmail messages Alfred Core has already synced, and offer to draft or schedule follow-up actions -- never sends or deletes anything on its own.
version: 0.1.0
author: Alfred contributors
license: Apache-2.0
metadata:
  hermes:
    tags: [alfred, gmail, productivity]
    related_skills: [morning-brief, weekly-review]
---

# Inbox triage

## Overview

`gmail-sync` already pulls unread Gmail headers/snippets into Alfred Core's
local storage on its own schedule; this skill's job is to read that already-
synced content and turn it into a short, useful summary -- not to sync Gmail
itself, and not to act on anything without the user's explicit say-so.

## When to use

- The user asks what's in their inbox, or to triage/summarize unread email.
- As part of a broader daily/weekly review alongside `morning-brief` and
  `weekly-review`.

## Steps

1. Call `connector_records_get` with `connector="gmail"` and
   `record_type="unread_message"` (default `limit=20` is usually enough; ask
   before requesting more).
2. Group or prioritize the results yourself -- unlike the morning brief, this
   content isn't pre-ranked. Sensible groupings: obviously time-sensitive
   (subject/snippet implies a deadline or a direct question), likely
   low-priority (newsletters, notifications), and everything else.
3. Summarize in a few lines per group, citing subject + sender, not full
   snippets verbatim unless the user asks to see one in full.
4. If the user wants to act on something (reply, archive-equivalent,
   schedule a follow-up task): a reply is a consequential send, so call
   `message_draft` or `message_send_propose` to preview it -- never assume
   approval. A follow-up task/reminder is automatic and reversible, so
   `task_upsert`/`reminder_set` can be called directly once the user
   confirms what they want tracked.

## Common pitfalls

- Treating `connector_records_get`'s results as already read/handled --
  Alfred Core marks nothing as read on your behalf; that's still whatever
  Gmail's own client does.
- Calling `message_send_propose` or `message_draft` without the user having
  asked for a reply -- triage is read-only by default.
- Assuming this tool sees *all* mail, not just unread messages `gmail-sync`
  captured since its last run -- say what you actually found, not what might
  exist beyond that window.

## Verification checklist

- [ ] `connector_records_get(connector="gmail", record_type="unread_message")`
      was called before any inbox content was summarized.
- [ ] No `message_draft`/`message_send_propose` call was made without an
      explicit user request to reply.
- [ ] The summary distinguishes "what I found in your synced unread mail"
      from anything else.
