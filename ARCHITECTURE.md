# Alfred architecture

Status: local implementation operating; slices 1-7 are built. Slack is the one connector still awaiting a real-account smoke test<br>
Last verified: 2026-08-18<br>
Goal: a cheap, local-first, open-source personal secretary that remembers, briefs, and acts across many clients and services.

> Canonical rule: store broadly, retrieve narrowly, act cautiously. Never put the full archive in an LLM prompt.

**Plain English:** SQLite is Alfred's private filing cabinet. A small knowledge graph is the map showing how the important things in that cabinet relate. Obsidian is an optional notebook where the owner can inspect and edit selected memories. Telegram is the free phone remote. The filing cabinet remains authoritative, so no model, chat app, or note-taking app can lock Alfred's memory away.

## 1. Decisions

1. **Do not fork Hermes yet.** Use upstream Hermes as the agent runtime and messaging gateway. Publish Alfred as a Hermes profile distribution plus a standalone `alfred-core` package. Fork only if a required core change cannot be implemented through a profile, skill, plugin, memory provider, or MCP boundary.
2. **Alfred Core owns the data.** Hermes, ChatGPT, Claude, Cursor, Telegram, and future voice clients are replaceable interfaces. None is the source of truth.
3. **Run a modular monolith on the always-on PC.** One Python service and one SQLite database are cheaper and easier to back up than microservices. Split services only after measured need.
4. **Use MCP in both directions.** Alfred serves stable personal-assistant tools to AI clients and consumes MCP tools where useful. Durable background sync should use official provider APIs, not depend on an interactive MCP client.
5. **Use Telegram first.** Its bot platform is free and supports long polling, so the PC needs no inbound public port. Add Slack and email later. iMessage is last because Apple has no comparable general-purpose bot API; practical bridges require an always-on Mac and add fragility.
6. **Use local inference by default.** Ollama handles embeddings, extraction, classification, summaries, and routine tool selection when hardware permits. The deployed Hermes profile currently uses Nous Portal's free model for interactive speed, with only local Ollama fallback: Alfred redacts common PII at the final subprocess boundary, enforces a hard monthly call count, caps packed context, and has no paid-provider fallback.
7. **Target Google Health API, not legacy Fitbit Web API.** The legacy Fitbit API is scheduled to stop syncing in September 2026.
8. **Start read-only.** Calendar, Canvas, GitHub, mail, and health begin as readers. Enable narrowly scoped writes only after audit logs, idempotency, and approvals work.
9. **License new Alfred code Apache-2.0.** Keep Hermes as an upstream MIT dependency and retain third-party notices. User data, tokens, memories, and logs never ship in the distribution.
10. **Build a lightweight knowledge graph inside SQLite.** Use Northwind-style typed, temporal entities and relationships with confidence and provenance. Do not add Neo4j or another graph database until measurements prove SQLite inadequate.
11. **Make Obsidian an optional, free projection.** Alfred never requires paid Obsidian Sync. Selected memories are ordinary Markdown, and Alfred continues to work with no Obsidian installation or with any Markdown editor.
12. **Keep academic history evidence-first.** Calendar and Canvas source events are immutable authority; compact JSON rollups and semantic memories are derived, rebuildable projections with source-event provenance. Cognee or another graph/vector engine may be benchmarked as an optional retriever, but never becomes canonical without measured retrieval gains and equivalent correction/forget guarantees.

## 2. System shape

```mermaid
flowchart LR
  TG[Telegram / Slack / email] --> HG[Hermes gateway]
  HG --> AC[Alfred Core]
  UI[CLI / future voice] --> AC
  AI[ChatGPT / Claude / Cursor] -->|MCP| AC
  AC --> MEM[(SQLite + FTS5 + vectors)]
  OBS[Obsidian / Markdown] <-->|selected notes| AC
  AC --> API[Calendar / Canvas API or private iCal / GitHub / Gmail / Health]
  AC --> MODELS[Ollama; optional cloud fallback]
  AC --> OUT[Scheduler + approval outbox]
  OUT --> HG
```

There are two independent planes:

- **Data plane — `alfred-core`:** memory, tasks, reminders, connectors, briefing, policy, audit, and MCP.
- **Agent plane — Hermes:** conversation loop, model/provider selection, skills, sandboxed task execution, gateway UX, and channel delivery.

Alfred Core is the sole owner of user schedules, reminders, retries, and the delivery outbox. It hands due output to a narrow Hermes delivery adapter. Hermes's native cron is disabled for user work (or limited to runtime health checks), preventing two job stores from drifting.

**Built, after the split happened anyway.** "Check again at 3 and text me" reached no Alfred scheduling tool — reminder detection matched only the literal word "remind" — so Hermes scheduled it in its own cron, a store Alfred does not run. The job sat there and never fired, which is exactly the drift this rule exists to prevent, and the user was told a job id and that a gateway was missing. Three fixes: scheduling intent is now recognised from ordinary phrasing (a future time plus an intent to be told); `task_schedule` gives Alfred a job kind for *work* rather than only for messages, since a reminder can only deliver text that already exists; and SOUL.md forbids using the runtime's own cron for user work and forbids surfacing job ids, cron expressions, or gateways at all.

A due `agent_task` deliberately does not run the agent inside `JobRunner`. An agent turn takes tens of seconds and spawns a subprocess against this same SQLite file, while `run_due` holds a write transaction — the same deadlock the two-phase Telegram intake exists to avoid. Instead it queues the instruction exactly as an inbound message is queued, so the ordinary bridge answers it on the next cycle with the whole normal pipeline. The reply is indistinguishable from one the owner asked for directly, which is the point: they asked for an answer at three o'clock, not a notification that a job ran.

Hermes connects to Alfred Core over local stdio MCP. Claude Desktop/Code and Cursor can use the same stdio entry point. Remote clients use Streamable HTTP. ChatGPT cannot connect directly to a local MCP server; use OpenAI's outbound-only Secure MCP Tunnel for a private installation. A public Alfred service would instead require stable HTTPS and OAuth.

ChatGPT and Claude subscriptions are not API credits. When they are MCP clients, their UI supplies the model. When Alfred runs unattended, it needs Ollama or separately billed API access.

The phone does not connect to SQLite or require Obsidian. It messages the Telegram bot, Hermes passes the request to Alfred Core on the PC, and Alfred replies with current memory and tasks. This works while the PC is awake. Optional Markdown sync can make selected Obsidian notes available offline on the phone, but it is not part of Alfred's control path.

## 3. Components

### Hermes distribution

The Alfred distribution contains only shareable behavior:

```text
distribution.yaml
SOUL.md                  # voice, boundaries, compact operating rules
config.yaml              # safe defaults; user overrides survive updates
mcp.json                 # starts/connects to alfred-core
skills/                  # morning brief, inbox triage, weekly review
cron/                    # optional runtime health checks only
README.md
```

Hermes's built-in memory remains a tiny bootstrap/persona cache. Alfred Core is canonical to avoid split-brain memory. Hermes session history may be imported into Alfred's event log, but Alfred never writes directly to Hermes internals.

### Alfred Core modules

| Module | Responsibility |
|---|---|
| `ingest` | Accept messages/webhooks/polls; deduplicate; append source events. |
| `memory` | Extract, validate, graph, version, retrieve, correct, export, and forget memories. |
| `vault` | Optionally import and project selected memories as portable Markdown. |
| `tasks` | Canonical tasks, commitments, reminders, and status transitions. |
| `connectors` | OAuth, sync cursors, normalized reads, and scoped actions. |
| `briefing` | Deterministic gather/rank, then one optional LLM writing pass. |
| `policy` | Identity, sensitivity, client scopes, approvals, and cloud egress. |
| `jobs` | Persistent schedules, retries, misfires, and delivery outbox. |
| `mcp` | One semantic tool surface over stdio and Streamable HTTP. |
| `models` | Ollama first; optional OpenAI/Anthropic-compatible adapters and budgets. |
| `audit` | Append-only tool/action records with redacted arguments and outcomes. |

Implementation baseline: Python, `uv`, Pydantic, official MCP Python SDK, SQLite in WAL mode, FTS5, and `sqlite-vec`. Use the same core functions from CLI, jobs, HTTP, and MCP; transports contain no business logic.

### Connector contract

Every connector implements the same narrow interface:

```text
authorize() -> account
sync(cursor) -> events + next_cursor
query(request) -> normalized records
propose(action) -> preview
execute(approved_action, idempotency_key) -> receipt
health() -> status
```

Each connector declares read/write capabilities, OAuth scopes, sensitivity, polling/webhook support, and rate limits. A normalized event always retains `source`, `account`, `external_id`, timestamps, and a deep link to the original.

**Built:** `connector_capabilities.py` is that declaration (`alfred connector-capabilities`, plus a section on the admin UI's connectors page). Nothing declared any of it before — `connector_health.py` answers *is it working*, and there was no answer at all to *what may it do*, so establishing whether Canvas can write, which Google scopes are actually requested, or which connector stores `sensitive` data meant reading eight modules and inferring. That is a security question rather than a curiosity, since section 8 gates writes behind approvals and filters retrieval by sensitivity. Five connectors can write (`google_calendar`, `gmail`, `github`, `telegram`, `slack`) and exactly one stores `sensitive` data (`google_health`, all three scopes read-only). Rate limits describe the bound that keeps usage small -- the page size actually requested (`maxResults` 100 for Gmail, `per_page` 50 for GitHub), plus the runner's exponential backoff on failure (30s doubling, capped at the connector's own interval) so a dead provider is not retried every cycle. Alfred does no quota accounting, and the declaration says so rather than implying a limit it enforces.

The declarations are cross-checked against the code they describe rather than trusted: a module defining an `Actions` class must declare that it writes, declared Google scopes must actually appear in the source that requests them, the writer set is pinned so adding one forces a deliberate edit, and any connector recording `sync_state` must be declared at all. A declaration that can silently drift is worse than none, because it invites trust it has not earned. (Verified by temporarily marking `github` read-only: the cross-check failed with the reason, as intended.) The deep-link half of this contract already held — across 3,452 real connector events, every Calendar, Gmail, and GitHub event carries one.

Prefer official APIs for reliable sync. Third-party MCP servers are acceptable for optional/ad-hoc tools, but must be allowlisted and cannot receive Alfred secrets other than their own scoped credential.

When an institution disables student-generated Canvas API tokens, Alfred may
use Canvas's official private iCalendar feed as a degraded read-only source.
The feed URL is a bearer secret held only in the operating-system keyring.
Sync uses bounded full snapshots plus ETag/Last-Modified validators; SQLite and
audit records retain no feed URL, query token, or event description. This path
provides dated assignments and events, not grades, submissions, To Do state, or
complete history, and must not be mistaken for full Canvas API parity.

## 4. Memory: what “remember everything” means

Alfred keeps an inspectable archive of consented inputs, not an impossible infinite prompt.

### Layers

1. **Raw event log:** immutable user messages and normalized source events. This is the recoverable truth.
2. **Documents/chunks:** searchable text or attachment metadata linked to raw events.
3. **Derived memory and graph:** facts, preferences, people, projects, decisions, habits, commitments, and their relationships. Each claim has provenance, confidence, validity dates, sensitivity, and version history.
4. **Working context:** a small request-specific pack assembled at runtime.

Corrections supersede old derived memories; they do not rewrite history. Conflicting facts remain visible until resolved. External mail/files are indexed by ID, metadata, and minimal excerpts by default rather than copied wholesale. Users can export or delete by source, time range, person, topic, or individual item.

**Built (export selectors):** individual item (`vault-export-entity`, `vault-export-memory`), source (`vault-export-source-event`), time range (`vault-export-range`), and topic (`vault-export-topic`). Time range filters on `created_at` — when Alfred recorded the claim, not when the fact became true — because "export everything from last March" means what was written down then; bounds are half-open so adjacent windows tile without double-counting. Topic reuses the same retrieval path a question would, rather than inventing a second matching rule that could diverge from what Alfred actually recalls. Every bulk selector shares one projection helper, so none of them can drift on the rule that matters: a wider net never downgrades a `secret`'s sensitivity or promotes a candidate. A topic receipt records *how* the set was chosen but never the query text, which can itself carry exactly the private phrasing the vault's sensitivity rules exist to exclude.

**Built (people exist now):** the prerequisite turned out not to be the edge at all — the graph held four calendars and a `self` node and *zero* people, so any "by person" query would have been a correct join over an empty table. `people.py` promotes Calendar's `creator`/`organizer` — a structured identity field keyed on an email address — into `person` entities, with the display name as the label and the address as an alias. They land **unconfirmed**, since derived claims stay quarantined until confirmed; naming someone yourself (`alfred memory-entity --type person`, or a `type: person` vault note) creates a **confirmed** one. An address that is already a `calendar` entity is skipped, which excludes both shared/group calendars and the owner's own address with no configuration.

**Gmail decides nobody's existence, but it does supply names.** Using Gmail to *create* people, measured over the real 629-message corpus, yielded 29 candidates of which 26 were brands (Amazon, Venmo, Pacsun, a16z), and the human-looking ones were the most dangerous: "Jamie Rivera" also arrives from `invites@invites.example`, "Jordan Lee via LinkedIn" from `messaging-digest-noreply@linkedin.com`. Keying a person to a bulk mailer is a false claim about identity, not merely noise; a display name there is the sender's branding.

Read the other way round it is safe and fixes a real defect. Calendar frequently supplies an address with **no** `displayName`, which left people labelled `relative@example.com` — an identifier, not a name anyone uses. Gmail is therefore consulted only for an address Calendar has already vouched for, purely to learn what that person is called; a brand cannot enter that way, because a brand is never a calendar organizer. (An earlier version of this note claimed no sender in the corpus was on a consumer mail domain. That was drawn from the 50 *active* — currently unread — records and was wrong for the full 629: `Jamie Rivera <jamie@example.com>` is real personal correspondence sitting in the archived set, and is exactly the record that now supplies their name.)

`MemoryGraph.rename_entity` (`alfred memory-rename`) covers whoever is left. It keeps the previous label as an alias, so existing `[[wiki links]]` and any prior reference keep resolving — renaming is not forgetting.

**Built (person selector):** all five of this section's selectors now exist. "About a person" is defined structurally: a memory is about someone when it came from a source event they organized or created, recorded as an `evidence` row linking that person to that event. `MemoryGraph.memories_about()` is then an exact join, and `alfred vault-export-person --entity-id` the selector.

Text matching was considered and rejected for a reason worth keeping: matching a person's name and aliases against memory text would treat "lunch near Robin's office" as a memory *about* Robin. Tolerable for an export, wrong for a deletion — and this selector serves both, so it takes the precise definition rather than the generous one. Coverage is therefore honest but partial: it answers for events with a recorded organizer and stays silent about mere mentions instead of guessing at both.

Measured on the live database: 1,042 of 2,609 memories carry an organizer, of which 772 are the owner (already the `self`/calendar identity) and 86 are the holidays calendar, leaving 184 memories across ten real people. Harvesting organizers from the immutable event log rather than only currently-active connector records is what found seven of those ten — the active snapshot describes who is on the calendar *now*, the log describes who ever was.

### Core records

| Record | Essential fields |
|---|---|
| `events` | source, external ID, occurred/ingested time, content, metadata, sensitivity, hash |
| `documents` | event ID, URI/path, MIME type, checksum, retention policy |
| `memories` | kind, statement, confidence, valid range, source refs, supersedes, status |
| `entities` / `edges` | people/projects/courses and typed relationships |
| `tasks` | title, state, priority, due time, source, recurrence, owner |
| `jobs` / `outbox` | schedule, next run, payload, attempts, idempotency key, delivery state |
| `tool_runs` / `approvals` | actor, client, tool, redacted input, decision, result, timestamp |
| `sync_state` | connector account, cursor, last success/error, backoff |

Retrieval is hybrid: FTS5 keyword match + vector similarity + time/importance/entity filters, followed by lightweight reranking. Embeddings are generated locally and versioned by model so they can be rebuilt. Promote to Postgres/pgvector only if multi-user operation or measured SQLite limits require it.

### Learning loop

- Save explicit statements and corrections immediately with provenance.
- Extract implicit preferences as low-confidence candidates; promote only after repetition or confirmation.
- Turn repeated workflows into versioned skills only after showing a diff for approval.
- Record accepted/rejected suggestions and retrieval misses as evaluation data.
- Do not fine-tune in the MVP. Better memory, retrieval, policies, and evals produce safer learning at far lower cost.

**Built:** the conversational runner now applies this loop after delivering
each reply. A deterministic local extractor handles explicit memory requests
and high-precision first-person preferences, identity facts, and goals.
Implicit observations remain `candidate` until corroborated by another source
event; sensitive observations never auto-promote and secret-shaped statements
are discarded. Confirmed recall is injected into the bounded runtime context,
while corrections supersede rather than overwrite and retrieval feedback is
stored append-only for evaluation. The extraction interface remains pluggable
so a stronger local structured-output model can be evaluated later without
changing Alfred's authoritative schema or promotion policy.

**Built (proposal-only):** each Hermes tool subprocess inherits a private turn
correlation ID. Successful MCP calls append only tool names, supplied argument
names, and a very small allowlist of non-content routing values; prompts,
message bodies, titles, people, addresses, dates, identifiers, and arbitrary
argument values are never workflow evidence. A local daily scan requires at
least three matching successful turns across two UTC days. Failed turns and
any turn containing a control/consequential tool such as `action_commit` are
discarded wholesale. Eligible patterns become versioned, inert `SKILL.md`
proposals with unified diffs and expiring approval records. Rejection is
audited and requires two more examples before Alfred asks again. This slice
deliberately has no activation or execution path: review can never change the
Hermes profile while unattended.

**Built:** successful Telegram responses are graded helpful, missing-context, or
wrong-context without anyone being asked. Two detectors reach that verdict: rule
matching on the owner's next message ("you missed the one from sam", "that's the
wrong week", "thanks, perfect"), and a check of the reply's own context pack that
flags a source with no successful sync behind it or one a full day stale. Alfred
stores one verdict per detector per response with only source names, freshness,
the name of the rule that fired, and opaque ranked record IDs, never prompt or
answer text; ranking still counts each response once. A verdict can make a
bounded ordering adjustment within existing Gmail/GitHub priority tiers; it
cannot select a new source, bypass low-priority-mail filters, or authorize any
action. Inline keyboards are reserved for approvals — the decisions Alfred is
not permitted to make alone — so a keyboard appearing at all means something is
actually waiting on the owner. An earlier slice asked for the same verdict with
`helpful` / `missing context` / `wrong context` buttons under every answer; the
data was useful and the request was the problem, since a signal that only
arrives when someone bothers mostly does not arrive. Taps on keyboards still
sitting in chat history are honored.

**Built (read side):** every bullet above records evaluation data, but until
now nothing read it back, so the data could not answer the question it was
collected for. `evaluation.py` (`alfred evaluation-status`, plus an admin-UI
page) summarizes all four signals over a window: response-feedback outcomes,
memory retrieval outcomes, workflow proposal acceptance, and implicit-candidate
promotion — plus which context sources were present when a vote landed, which
is the join `response_context` and `response_feedback` were designed for.
Deliberately association rather than attribution: a turn packs several sources
at once, so a source appearing beside `wrong_context` is where to start
looking, never proof it caused the miss. Verdicts the owner reached — stated or
inferred — carry the helpful rate and the source attribution; answers Alfred
flagged against its own stale pack are counted apart, since a connector going
quiet is connector health and folding it in would read as a week of disliked
answers. The report runs no model, writes nothing, and changes no ranking;
deciding what to do about a bad number stays a human judgment. It is content-free like the tables it reads (outcomes,
counts, source names, opaque record IDs), so it is safe to paste into an
issue. A metric with no votes yet reports null rather than zero — an unmeasured
system must not read as a failing one.

## 5. Knowledge graph and Obsidian

### One memory, four views

| Layer | Job | Authoritative? |
|---|---|---|
| SQLite events/documents | Receipts: what was actually said or synced | Yes; historical evidence |
| SQLite knowledge graph | Current structured understanding of people, projects, courses, goals, and preferences | Yes; versioned derived state |
| Obsidian/Markdown vault | Human-readable notes and selected graph projections | Only for notes the user authors or explicitly edits |
| Runtime retrieval | The tiny evidence pack relevant to the current question | No; temporary |

The graph is a **layer over evidence**, not a replacement for messages and documents. If extraction improves, Alfred can rebuild derived graph records from retained evidence. Obsidian is not the database, job queue, audit log, or secret store.

### Minimal graph model

Keep the graph in the same SQLite database as the rest of Alfred:

| Table | Minimum contents |
|---|---|
| `entities` | stable ID, type, label, properties, domain tags, sensitivity, confidence, confirmed flag, created/updated times |
| `aliases` | entity ID, alternate label, source, confidence |
| `relationships` | source entity, predicate, target entity, state/event kind, single/multi cardinality, valid-from/to, domain, sensitivity, confidence, confirmed flag |
| `evidence` | memory/entity/relationship ID, event/document/chunk reference, source account, extraction version, excerpt hash |
| `type_registry` / `relation_registry` | allowed types, predicates, constraints, and plain-language descriptions |
| `memory_history` | proposed, accepted, superseded, rejected, or deleted versions and actor |

Graph rules:

- Create one permanent `self` entity representing the owner.
- Make durable named things nodes: people, organizations, courses, projects, goals, documents, tasks, and meaningful preferences. Keep simple values such as an email address, due date, or status as properties instead of turning everything into a node.
- Every relationship is typed and temporal. An **event** records something that happened; a **state** describes what is currently true. A single-cardinality state, such as `self studies_at school`, automatically closes the previous active edge when corrected.
- Never silently overwrite history. Corrections supersede memories or close old state edges while evidence remains inspectable.
- Attach provenance, domain, confidence, confirmation, and sensitivity to every inferred claim. Explicit user statements and designated user-authored notes are high-trust evidence. Model inferences begin as candidates and remain softly quarantined until confirmed or supported repeatedly.
- Let an LLM propose strict structured objects; deterministic validation and registry rules decide what enters the graph. New entity or relation types remain unconfirmed until approved.
- Use conservative entity resolution. It is safer to temporarily keep two possible "Alex" entities than to merge two people incorrectly.

Do not graph every message or add a graph server in v1. Also defer the in-memory Graphology mirror, PageRank, community detection, complex weighting, a giant ontology, and graph visualization. Add them only for a measured retrieval or product need.

### Retrieval

For each request Alfred applies client/sensitivity permissions, finds anchor records with FTS5 and local embeddings, expands only one or two graph hops, reranks by relevance/recency/confidence, and returns a token-budgeted pack with evidence links. This gets the benefit of a graph without sending the entire graph—or life history—to a model.

### Obsidian vault

The optional vault is plain Markdown and stays useful without Alfred:

```text
alfred-vault/
  Inbox/
  People/
  Projects/
  Courses/
  Decisions/
  Daily/
  Generated/
```

Every managed or linked note gets a stable ID in YAML frontmatter; normal `[[wiki links]]` can express relationships:

```yaml
---
alfred_id: project_01J...
type: project
sensitivity: personal
managed: false
updated: 2026-08-11T09:00:00-04:00
---
```

Synchronization rules:

- A file watcher hashes changed notes, parses frontmatter and links, appends a file event, then proposes validated memory/graph updates. User-authored designated notes count as confirmed evidence, but do not bypass action permissions.

  **Built (links, conservatively):** `[[Note]]`, `[[Note|display]]`, and `[[Note#Heading]]` are parsed from user-authored notes and, when a target names *exactly one* existing entity by label or alias, recorded as evidence against that note's source event — a link the owner typed is an explicit statement that the note concerns that entity. Three refusals are deliberate. It never creates an entity, because a filename is not evidence that a thing exists; an unknown target is counted as unresolved instead. An ambiguous name resolves to nothing, per this section's "two possible Alex entities beat one wrong merge". And it never manufactures a relationship edge, because what a bare link *means* is a typed, registry-validated, temporal decision, and inventing a predicate from `[[Alex]]` is exactly the guessing these rules exist to prevent. Links inside Alfred's own `managed: true` output are ignored — that is Alfred's writing, not the owner's testimony. `alfred vault-import` reports `linked` and `unresolved_links` so the refusals are visible rather than silent.
- Alfred writes whole generated notes only under `Generated/`, or writes within clearly marked managed blocks elsewhere. It never blindly overwrites user prose.
- Stable IDs plus content hashes prevent duplicates. A stale or concurrent edit creates a conflict copy for review. A future open-source Obsidian plugin should use the Vault API's conflict-safe processing methods.
- Deleting a projected note does not secretly erase raw history. The explicit `forget` command handles scoped deletion and records an audit tombstone.
- Never place credentials, connector cursors, audit logs, the SQLite database, raw message archives, or detailed raw health data in the vault. Project only selected summaries and notes.

### Phone access without paying for Obsidian

The default phone experience is Telegram, which exposes the full assistant while the PC is online. Obsidian mobile access is optional and only needed to browse or edit the Markdown notebook offline.

Alfred does **not** use the paid Obsidian Sync service. If mobile Markdown sync is later wanted:

1. Sync only `alfred-vault/`, never `alfred.db`, secrets, or logs.
2. Select one maintained free method for the actual phone OS; do not mix sync systems on one vault.
3. For a Windows PC plus iPhone/iPad, do not make iCloud Drive the default because Obsidian documents duplication/corruption risks on Windows. Prefer a vetted, mobile-compatible open-source community adapter backed by a self-hosted WebDAV/CouchDB service over a private encrypted network.
4. For Android, use the same self-hosted route or a maintained folder-sync tool. Treat the sync adapter as replaceable.

That optional route has no required software subscription, although the PC/storage and network still have real operating costs. The phone retains its last synchronized Markdown copy offline; live Alfred queries still require Alfred Core to be running.

**Built:** `deploy/couchdb/` provisions that self-hosted CouchDB service (loopback-bound by default; reach it from a phone through a VPN, never a forwarded port), with setup docs covering the Self-hosted LiveSync plugin this design names. Alfred's own code never becomes a sync client — the plugin does that job entirely, client-side, exactly as "treat the sync adapter as replaceable" calls for. `alfred vault-sync-status` is the one piece of Alfred code involved: an unauthenticated reachability check, mirroring `connector-status` for every other external dependency.

### Open-source boundary

Obsidian itself is a separate, optional proprietary application; this does not prevent Alfred from being open-source. Alfred Core, its Markdown adapter, and any Alfred Obsidian plugin use Apache-2.0, call only documented interfaces, and do not redistribute Obsidian. Plain Markdown plus JSON exports avoid lock-in, and a fully open-source editor can replace Obsidian. Northwind is design inspiration: borrow the data invariants and retrieval pattern described by the owner, but copy no Northwind code or assets unless their license is independently verified as compatible. Personal vaults, databases, tokens, and generated backups are gitignored and never included in releases.

## 6. Token and cost controls

Default extra context budget, excluding the current message and selected tool schemas: **3,000 tokens**.

| Context slice | Max tokens |
|---|---:|
| Persona + hard rules | 250 |
| Stable user profile | 350 |
| Active goals/tasks | 400 |
| Retrieved memories | 900 |
| Conversation summary | 700 |
| Reserve | 400 |

Rules:

- Retrieve summaries first; fetch raw excerpts only when evidence is needed.
- Expose only the tool group relevant to the turn, ideally no more than 8 tools.
- Cache embeddings, connector results, daily aggregates, and conversation summaries.
- Use deterministic code for sync, sorting, due-date logic, and threshold alerts.
- Use one small/local model pass for extraction and at most one for a routine brief.

**Operating now:** bridge context is mechanically capped at 10,000 serialized characters (a conservative tokenizer-independent approximation of this section's 3,000-token budget). Gmail and academic history are ranked and compacted before packing. Every outbound Hermes prompt passes through Alfred's redactor, and the deployed profile has a 1,000-turn monthly hard cap plus a local-only fallback. Dynamic per-turn MCP tool narrowing is built: Alfred deterministically selects from bounded task, calendar, communication, memory, and status groups, passes at most eight names through the child-process environment, and `alfred-mcp` registers only those tools for that Hermes turn. Casual conversation is a separate `poolside/laguna-xs-2.1:free` route with reasoning disabled, zero Alfred MCP tools, exact local FTS memory recall, and up to eight completed exchanges from the last week; explicit work retains hybrid vector recall, the profile's stronger `stepfun/step-3.7-flash:free` default, the smaller two-exchange context, and guarded tools. Other MCP clients retain their full policy-scoped surface. Content-free latency telemetry correlates the Telegram receipt, bridge phases, and first delivered reply; `alfred latency-status` exposes recent samples and p50/p95 summaries without storing message content in telemetry. With chat configured, periodic connectors remain sequential but run on one bounded background worker so their batch cannot block intake; Telegram uses a 10-second server poll, a 12-second read ceiling, and a one-second failed-poll retry. A bounded persistent-Hermes spike rejected ACP/`serve` for production: ACP fixes tools at session creation, and two isolated-session trials exceeded 30 seconds before prompting because each session constructs a fresh agent. Reusing a session would violate the bounded-conversation and per-turn-tool invariants, so the redacted, timed one-shot runner remains the safe path until upstream supports per-prompt tool selection or cheap isolated sessions.

**Routing correction (observed live):** "casual" has to mean *answerable with no outside information*, not merely *mentions no connector*. A question about a tennis tournament matched no work term, fell through to the casual lane's small no-tool model, and spent 120 seconds failing to answer something that needed a web search. External-lookup phrasing (scores, weather, news, "look it up", a question following a lookup offer) now routes to the capable lane even though no *Alfred* tool serves it — Hermes's own web search does. Two related fixes came out of the same turn: connector selection now keys off the current request alone, because history-driven selection let a days-old GitHub conversation load the GitHub pack into that tennis question, and the casual lane's seven-day window made that the common case; and the casual lane packs no connector context at all, since with zero Alfred tools it is unusable weight on the one path whose entire purpose is speed. The pack is an optimization that saves a tool round-trip, never the only route to a connector, so selecting too narrowly costs one extra call while selecting too widely costs every unrelated turn.

**Same bug, second location (found by re-testing the fixed turn):** *tool* selection scanned full history including Alfred's own replies, which created a feedback loop worth naming — Alfred's reply "i handle the boring stuff like email, github, tasks, calendar, and reminders" put four topic words into the pool, so the next question was handed `agenda_get`, `brief_get`, and `connector_records_get` and spent its budget calling tools that could not help. A model's description of itself is not evidence about what the user wants. Tool selection now uses the request alone unless the request is too short to stand on its own (≤8 words, "yeah do that"), in which case it still inherits the previous exchange — that is the one case where the topic exists only in the proposal being confirmed. External-lookup turns also skip semantic memory recall entirely, since no stored memory answers "who's playing tomorrow" and the embedding round-trip alone measured 7.2 seconds of a 103-second turn.

**Reactions are a receipt, not a reward.** They fire *before* the agent runs and do not depend on its outcome: a turn can take ninety seconds, and a nod arriving at the end confirms nothing the reply itself does not. The emoji reports the lane — a salute when Alfred is going to use tools, a thumbs up when it is only talking — so the gesture carries information. Two earlier versions were wrong in instructive ways: gated on tool-backed turns only (about one reaction per 28 real messages, so it was never once observed), then gated on success (arriving up to two minutes late).
- Escalate to the configured cloud model only for turns that need Hermes; deterministic calendar reads, filtering, sync, ranking, and memory extraction remain local. **Built:** the bridge applies `Redactor` at its final subprocess boundary, enforces a 1,000-call monthly cap, and records successful external turns. `models.GuardedCloudProvider` remains the stricter cost-estimating wrapper for cloud providers Alfred Core calls directly.
- Track input/output tokens and estimated cost per run. Default monthly cloud budget is `$0`; fail closed when the configured cap is reached. **Built:** `GuardedCloudProvider` checks month-to-date spend (summed from its own audit records) before every call and refuses once the cap is already met, defaulting to `$0` so an unconfigured instance never calls out. This checks the cap before each call, not a per-call ceiling, so a single very large call can still exceed the cap within one run.

**Two holes in that accounting, both closed after the first live paid turn.** The monthly Hermes call cap counted only *successful* turns, because the audit row it reads was written on the success path alone. A turn that reached the provider and then timed out, exited non-zero, or produced nothing had already cost money and consumed no budget — and since one Hermes failure can be three billable retries, a failing model could bill indefinitely while the counter stood still. Every turn that actually spawned the subprocess is now audited and counted; a missing binary still is not, since nothing ran and counting it would let one restart loop burn a month's allowance without a single model call.

Separately, Hermes reports an upstream failure by printing it and exiting zero, so from Alfred's side an outage was indistinguishable from an answer — both are text on stdout. The misrouted-provider bug produced exactly this: `API call failed after 3 retries: HTTP 404: Model ... requires available credits` would have been delivered to the owner as Alfred's own reply, naming a vendor never configured. Output matching a provider-failure shape *at its start* is now treated as a failed turn, so the bridge sends its honest "couldn't reach my model" line instead. Anchoring matters: a reply that merely discusses an error is still delivered, and the asymmetry justifies the heuristic — a false positive costs one retry, a false negative hands over raw vendor billing text.

Local software can cost `$0/month`; electricity, existing subscriptions, a domain, or optional cloud inference are not free. The PC must be awake for live replies and scheduled work. Persistent jobs run missed executions once after restart and label the result late.

**Built, opt-in:** a paid model for *work* turns only, off unless every one of `--hermes-work-model`, `--hermes-work-provider`, and `--hermes-provider-key-secret` is supplied and the key is readable; anything missing degrades silently to the free tier rather than failing a turn. Casual turns stay on the free conversational model, so the common "yo" costs nothing.

The cost decision this note previously deferred has been made, on measurement rather than preference: the free tier answered in 43-105 seconds with the variance of a queue, while Gemini Flash through OpenRouter answered the same turns in about 6 seconds at roughly `$0.0026` each. The owner set a spend limit at the provider and accepted the trade for tool-calling latency.

Two caps guard it locally, both reading the same audit rows: a monthly call count and a monthly dollar ceiling (`--hermes-monthly-budget-usd`), summed from Hermes's own per-turn usage report. A call count alone was only ever a proxy — measured turns varied by an order of magnitude in tokens — and every turn that actually spawned the subprocess is counted, including failures, since a failed turn is still billed and one Hermes failure can be three billable retries. A missing binary is deliberately not counted: nothing ran, and counting it would let a restart loop burn the month's allowance without a single model call.

The credential never touches the repository. It goes OS keyring → subprocess environment → gone, never into `hermes-profile/config.yaml` (which is version-controlled), SQLite, or the audit log; the audit records the model name only. And a provider outage is not delivered as an answer: Hermes reports an upstream failure by printing it and exiting zero, so a misrouted model once produced "requires available credits" as Alfred's own reply, naming a vendor the owner had never configured. Output matching a provider-failure shape at the start of the response now fails the turn instead.

**Built, worth flagging:** `hermes-profile/`'s `hermes-telegram` toolset is Hermes's own default and already includes full `terminal`/`process` tools and, for a while, a BrowserOS neo MCP connection (installed, verified driving a real page, then deliberately disconnected: its server injects ~2,900 characters of instructions into *every* Hermes turn plus its tool schemas, paid on "yo" as much as on real browser work, while Alfred already reaches Gmail/Calendar/GitHub/Canvas through checked, approvable connectors. Re-add when a task genuinely needs a logged-in page.) — real shell execution and real browser automation against whatever you're logged into, reachable by anything that can message this Telegram bot. Neither goes through Alfred Core's own policy-gated, propose/approve MCP surface (section 7-8) — that boundary only covers tools Alfred Core itself exposes. Compounding that: every Telegram turn runs Hermes one-shot (`hermes -z`, no TTY), which unconditionally sets `HERMES_YOLO_MODE=1` and so auto-approves the entire normal "dangerous command" tier (`rm -rf`, `chmod -R 777`, `git reset --hard`, `curl | sh`, ...) with zero confirmation. The only floor that survives yolo is the tiny code-shipped hardline blocklist plus `config.yaml`'s own `approvals.deny` — now populated with ~70 broad substring patterns covering the categories above (see `hermes-profile/README.md` points 8-9). Neither BrowserOS nor the terminal tools' *reach* is scoped down by this — only specific destructive commands are blocked outright.

**Built after three silent outages in one week.** Alfred had no MCP server registered in Hermes at all, then a policy grant that had drifted to 22 tools against 33 served, then a per-turn tool filter that never crossed into the MCP subprocess. Every layer worked in isolation and none could see the chain, so nothing raised: the boundary raises for its caller, the caller is a language model, and a model that cannot reach a tool apologises for an outage it invented rather than reporting a broken install. `alfred preflight` walks the links in order — is Hermes configured to start the server, can an active client actually call what is served — and names the broken one and the command that repairs it. Registration is the check worth having most, because it un-fixes itself: `hermes profile update` rewrites that config, and nothing else would notice the key going missing again.

The single Python service from decision 3 can run either as a foreground CLI process (`alfred run`, kept alive by a login-triggered Task Scheduler entry or a terminal left open) or, now, as a real installed Windows service (`alfred-service`, via `winservice.py`) that survives logoff and reboot with no logged-in session. The service is a thin wrapper around the same `AlfredRunner` construction/cleanup logic `alfred run` itself uses — not a second implementation — so the two paths cannot quietly diverge. It stops cleanly via `AlfredRunner.run_forever()`'s `stop_check` hook, which the service's `SvcStop` handler sets; restart-on-crash is Windows' own service recovery configuration (`sc.exe failure`), not code Alfred runs itself.

## 7. MCP surface

Expose semantic Alfred operations rather than every provider endpoint:

```text
memory_search        profile_get          remember
forget               agenda_get          task_upsert
task_complete        reminder_set         brief_get
message_draft        action_commit        connector_status
```

`message_draft` and other consequential operations return a preview. Telegram attaches owner-bound approve/cancel buttons to proposals created during that exact turn; a durable worker escrows the one-time approval token in Windows Credential Manager and calls the same idempotent executor as `action_commit`. The conversational model never receives `action_commit` in its per-turn tool list. MCP annotations mark tools read-only/destructive where supported. **Built:** all 19 tools carry `readOnlyHint`/`destructiveHint`/`idempotentHint`/`openWorldHint`; until now none did, which reads to a client as "unknown" and leaves one that would have paused before a destructive call with nothing to pause on. The classification follows the propose/commit split exactly: `action_commit` is the only tool marked destructive *and* the only one marked open-world, because a proposal writes an approval record and contacts no provider — calling `message_send_propose` destructive would cry wolf on the one call that is genuinely safe to make. It is also marked idempotent, since a completed approval replays its exact receipt rather than acting twice. These are hints about intent, not enforcement: policy checks, previews, and one-time approval tokens remain the actual boundary, and a client that ignores annotations entirely still cannot commit anything without a token. Per-client scopes prevent a coding client from receiving health data or sending personal messages unless explicitly granted. **Built after that scoping silently broke the agent:** the grant is a separate list from the served tools and nothing compared them, so it drifted to 22 against 33 served. Every recently built tool was routed to, offered, and refused at the boundary, and nothing reported it anywhere -- `require_read` raises for the caller, the caller is a model, and the model apologised for a connector outage it had invented. `alfred policy-coverage` now reports tools no active client can call. A tool missing from *one* client stays informational, because narrow scoping is the point of this paragraph; the actionable finding is a tool reachable by nobody, which is only ever an oversight.

Transport policy:

- Local Hermes, Claude, Cursor: stdio.
- Deployed/private clients: Streamable HTTP on `/mcp`; never build new work on legacy SSE. **Built:** `alfred mcp-http-run` serves it on `127.0.0.1:8000/mcp`, host not configurable. A shared bearer token (`alfred mcp-http-token-generate`, kept in the OS credential store) authenticates every request outside FastMCP's own handling, and FastMCP's built-in DNS-rebinding protection is active by default for a loopback host.
- Local server binds `127.0.0.1` only. Validate `Origin`, authenticate every remote request, and use OAuth 2.1/RFC 9728 metadata for public remote access. The bearer token above satisfies "authenticate every remote request" for this loopback-only phase; OAuth 2.1/RFC 9728 remains unbuilt and is reserved for actual public remote access, a separate, larger undertaking.
- ChatGPT private access: Secure MCP Tunnel. **Built:** `deploy/openai-tunnel/` documents pointing OpenAI's own `tunnel-client` (not vendored here) at `alfred-mcp --client-id <id>` over stdio; `alfred-mcp` gained a `--client-id` flag (default unchanged: `local-mcp`) so the tunnel gets its own scoped grant instead of sharing Claude/Cursor's. Whether a given ChatGPT plan can actually add a custom MCP connector is an OpenAI account question this repo has no control over. Public distribution: HTTPS + OAuth and a separate security review.

## 8. Permissions and safety

| Action class | Default |
|---|---|
| Read/sync approved sources | Automatic |
| Store user message; derive low-risk memory | Automatic, visible in audit |
| Create/edit local task or reminder | Automatic and reversible |
| Calendar write, GitHub write, file mutation | Preview + confirm initially |
| Send email/message, submit school work, publish/push | Always preview + confirm |
| Delete data, spend money, merge, security/credential change | Strong confirm; never unattended |
| Health writes | Disabled in v1 |

Additional invariants:

- Secrets live in the OS credential store, never SQLite, logs, prompts, Markdown, or git.
- Require full-disk encryption and encrypted, versioned backups. Test restore monthly. **Built and scheduled:** `scripts/verify-backup.ps1` runs the drill through the `Alfred Backup Verify` task every four weeks, an hour after the nightly `Alfred Backup` snapshot so it always checks a fresh one. It appends one JSON line per run to `.alfred/backup-verify.log` and throws on failure, so a bad backup becomes a failed scheduled task rather than a cheerful run that logged bad news. `alfred backup-verify` (`--backup PATH` or `--latest-in DIR`) rehearses a restore into a throwaway copy and never touches the live database. That distinction is the whole reason it exists: the only previous way to test a restore was `backup-restore-execute`, which is approval-gated and *overwrites the live database* — a test nobody sensible runs on a working system, which is exactly how backups stay unverified until the day they are needed. The drill answers the questions that matter rather than merely parsing the file: does the stored key still open it, is the SQLite intact, do migrations apply, does the audit hash chain still verify, and is the data actually present (row counts for `events`/`tool_runs`/`memories`/`entities`/`tasks`) — a backup that decrypts cleanly into an empty database is worthless. Expected failures are reported rather than raised, so a scheduled drill surfaces a broken backup as a report instead of a crashed job, and the CLI exits non-zero so it is noticed.
- Tag data `public`, `personal`, `sensitive`, or `secret`; model and client policies filter before retrieval.
- Treat email, web pages, issues, and documents as untrusted content. They cannot override Alfred rules or authorize tools.
- All mutations use idempotency keys and a transactional outbox; retry only safe/retriable failures.
- Default-deny channel identity. Pair Telegram/Slack user IDs locally.
- Every answer based on synced data includes source links and freshness; stale sync is explicit.
- Response feedback is evaluation data only, whether stated or inferred; no verdict can approve, execute, or retry an action, and no detector may ask the model to reach one.

## 9. Connector order

| Phase | Connectors and behavior |
|---|---|
| MVP | Telegram long polling; local tasks/reminders; Ollama; manual memory import |
| Assistant | Google Calendar current + bounded history read; Canvas upcoming/missing + accessible course assignment history; morning brief |
| Developer | GitHub notifications with a dedicated classic PAT (`notifications` scope); issue/PR writes with a separate repo-scoped fine-grained PAT, then GitHub App for distribution |
| Communications | Gmail read/draft, then send approval; Slack Socket Mode; inbound Alfred email |
| Wearable | Google Health API read-only: sleep/activity/heart metrics with strict sensitive-data policy. **Built and now verified against a real account:** 1,172 synced points from a Fitbit Air and Apple HealthKit, across steps, sleep, and resting heart rate. The field-name guesses held — sleep parses to a duration and its stages — which is why `_normalize_data_point` still keeps each point's full raw JSON: the guesses were right, not proven exhaustive, and the raw copy is what allowed this to be checked at all rather than re-synced. |
| Clients | Claude/Cursor stdio MCP; ChatGPT via Secure MCP Tunnel or hosted OAuth endpoint |
| Later | Voice input/output; iMessage only through a separately isolated Mac bridge |

Canvas starts with an institution-issued personal token only for the owner's private installation if school policy permits it. A distributed app must use institution-approved OAuth; [Canvas explicitly disallows](https://developerdocs.instructure.com/services/canvas/oauth2/file.oauth) asking other users to paste manually generated tokens. Store assignments and missing-submission state, not grades or course files unless requested.

The morning brief gathers data without an LLM, ranks due/overdue items using timezone-aware rules, detects calendar conflicts, and can then ask a local model to write a short brief. It reports per-connector freshness and includes available Calendar, Canvas, or GitHub links. Optional sleep context from Google Health is a no-op when no sleep records overlap last night. The live wearable-linked smoke test has now passed: the brief rendered "7h 42m last night — Google Health (includes light)" from real synced records.

## 10. Build slices

1. **Walking skeleton:** Alfred Core, SQLite migrations, stdio MCP, CLI, audit log, and tests.
2. **First useful loop:** Telegram message → event log → task/reminder → Telegram receipt.
3. **Daily secretary:** Google Calendar + Canvas read sync → persistent morning brief → late/missed-run recovery.
4. **Memory:** typed temporal graph, provenance/confidence, hybrid retrieval, corrections, export/forget, local embeddings, and evaluation fixtures. **Operating:** 1,730 current Calendar/Canvas facts were promoted into source-linked memories and embedded locally with `nomic-embed-text`; provider updates supersede old derived facts, calendar/course groups connect to the owner graph, and explicit retrieval feedback now affects ranking only within the query's candidate set. Canvas uses the same pipeline as soon as its feed publishes events.
5. **Safe action:** drafts, approvals, idempotent outbox, then Gmail/GitHub/Calendar writes. **Built:** Calendar creates recover across the provider/receipt crash window through a stable provider event ID; Gmail drafts and sends recover through stable RFC 2822 Message-ID lookups; and GitHub issues and PR comments recover through hidden, exact body markers. Every action remains preview-then-human-approval, with exact receipt replay after completion and fail-closed recovery when provider evidence is absent or ambiguous. Telegram now supplies the approval surface directly on the proposal, bound to the originating chat and user; the model cannot commit its own proposal. Reminders and briefs carry an explicit channel destination instead of assuming Telegram.
6. **More surfaces:** Claude/Cursor stdio, paired Slack Socket Mode, inbound Alfred email, loopback Streamable HTTP, and the private ChatGPT tunnel are built; Slack and Google Health still await real-account smoke tests. The installed Hermes profile uses Nous Portal free tier primary with local `qwen3.5:9b` fallback and no paid-provider fallback. Its supported read-only web search uses the no-key DDGS backend. Hermes's native Telegram gateway still hangs on this platform, so Alfred Core owns the proven transport and `hermes_bridge.py` invokes Hermes outside the intake transaction, then delivers through the transactional outbox.
7. **Notebook and polish:** Obsidian/Markdown projection and import plus encrypted local backup/restore are built. A Windows installer script (`scripts/install.ps1`) now covers venv creation, package install, database init, and an optional Task Scheduler entry, touching no provider and previewable with `-WhatIf`. Public documentation for outside contributors — `CONTRIBUTING.md`, `SECURITY.md`, and GitHub issue/PR templates — is also built. Release signing is built and automated end to end (`.github/workflows/release.yml` builds, tests, and attests keyless SLSA provenance for a tagged release via GitHub's own OIDC/Sigstore integration, needing no maintainer-held key), plus a `ci.yml` that runs the full test suite on every push and PR; `v0.1.0` was cut on 2026-08-12 and `v0.2.0` on 2026-08-19, each with an sdist, a wheel, and keyless SLSA provenance; `RELEASING.md` documents cutting the next one. A release is preceded by an identifier audit rather than an assumption of cleanliness — the `v0.2.0` sweep found real addresses and names that had accumulated in fixtures and prose, some of them added the same day. The optional free mobile sync adapter is built (see section 5): a self-hosted, OS-agnostic CouchDB service plus setup docs for a vetted community sync plugin, since the sync mechanism itself does not depend on which phone OS an operator has — only which existing third-party client app they install. Admin UI is built (`admin_ui.py`, `alfred admin-ui-run`): a read-only dashboard (agenda, pending approvals, connector health, recent audit trail), loopback-only by default like `mcp-http-run`, but with cookie-delivered auth (a browser navigation can't set a custom header) and, unlike every other local HTTP surface in this codebase, a configurable `--host` — since this one is meant for a person to look at, sometimes from a phone over a VPN, and `127.0.0.1` is unreachable from another device regardless of network route. The dashboard remains read-only; phone approvals now happen through owner-bound Telegram buttons, while the CLI remains available for local administration. The shape (one page per concern, no spec existed in this doc before now) was a deliberate choice made when the owner asked for it, not a documented requirement like every other item finished this session.

The first acceptance path is now deterministic and covered through intake: “my paper is due Friday; remind me Thursday” creates one source-linked task with a Friday deadline, a Thursday reminder, and an explicit provenance-linked deadline memory without spending a model call. Automated tests cover parsing, persistence, memory extraction, missed-run recovery, and briefing after restart. The remaining operator check is the real-time wait for an actual Thursday delivery.

<<<<<<< HEAD
**Current deployment:** the repaired environment passes 383 tests. Alfred runs as a hidden per-user `Alfred` logon task because this session cannot control the installed administrator-owned Windows service; a duplicate runtime was removed and Telegram returned healthy. A separate `Alfred Backup` task creates an encrypted timestamped backup daily at 02:30, with the key in Windows Credential Manager. The first encrypted backup and an isolated restore/integrity drill both completed successfully. Calendar path IDs are now URL-encoded, so the `#` in Google's US Holidays calendar no longer truncates its REST path; a live current and three-year history refresh completed with all 17 connector-health entries green. Generic same-day calendar checks now render a compact three-item agenda without raw provider links or timestamps. Calendar/Gmail/GitHub proposals have durable Telegram approve/cancel controls, and Hermes web search is configured through DDGS. Casual Telegram messages use Telegram's native typing status instead of synthetic progress text, use Laguna XS with extended continuity and fast exact-memory recall, and reserve Step plus hybrid vector recall for tool-backed or explicit memory work. Connector batches run off the chat path, and a hung Telegram poll now times out near 12 seconds and retries after one second; tool-backed replies end with one relevant follow-up question.
=======
**Current deployment:** the repaired environment passes 592 tests. Alfred runs as a hidden per-user `Alfred` logon task because this session cannot control the installed administrator-owned Windows service; a duplicate runtime was removed and Telegram returned healthy. A separate `Alfred Backup` task creates an encrypted timestamped backup daily at 02:30, with the key in Windows Credential Manager. The first encrypted backup and an isolated restore/integrity drill both completed successfully. Calendar path IDs are now URL-encoded, so the `#` in Google's US Holidays calendar no longer truncates its REST path; a live current and three-year history refresh completed with all 17 connector-health entries green. Generic same-day calendar checks now render a compact three-item agenda without raw provider links or timestamps. Calendar/Gmail/GitHub proposals have durable Telegram approve/cancel controls, and Hermes web search is configured through DDGS. Casual Telegram messages use Telegram's native typing status instead of synthetic progress text, use Laguna XS with extended continuity and fast exact-memory recall, and reserve Step plus hybrid vector recall for tool-backed or explicit memory work. Connector batches run off the chat path, and a hung Telegram poll now times out near 12 seconds and retries after one second; tool-backed replies end with one relevant follow-up question.
>>>>>>> 08570af (Count every billable turn, and stop delivering provider errors as answers)

## 11. Fork rule and escape hatch

Stay on a pinned upstream Hermes release and upgrade deliberately. Build Alfred-specific behavior outside Hermes. Fork only when all are true:

1. A required feature belongs in the runtime rather than Alfred Core.
2. Hermes exposes no stable plugin/profile/MCP hook for it.
3. An upstream contribution was rejected or cannot meet the release need.
4. Alfred can fund ongoing security fixes and upstream merge work.

If that happens, preserve Hermes's MIT notices, keep an `upstream` git remote, minimize core diffs, and continue treating Alfred Core's database and interfaces as portable. This makes replacing or rebasing the runtime possible.

## 12. Open questions before implementation

These change configuration, not the architecture:

- PC RAM, GPU/VRAM, available disk, and sleep behavior (determines local model size).
- Exact wearable model/account and whether its data is present in Google Health API.
- School Canvas base URL and whether personal tokens are permitted.
- Google account type (personal vs. Workspace changes what an org admin can restrict). Calendar/Gmail scopes default to `calendar.events` (read/write, matching the approval-gated event write), `gmail.readonly`, and `gmail.compose` (matching the approval-gated draft write); override with `alfred google-auth --scope` if a narrower or different grant is preferred.
- Current ChatGPT/Claude plans; ChatGPT write-capable custom MCP access is plan-dependent.
- Phone OS and whether offline Markdown editing is important beyond Telegram; this selects the optional free vault-sync adapter.
- Desired morning-brief time, timezone, quiet hours (`ALFRED_QUIET_HOURS_*`), and cloud-spend ceiling.

## 13. Primary references

- [Hermes Agent repository and MIT license](https://github.com/NousResearch/hermes-agent)
- [Hermes profile distributions](https://hermes-agent.nousresearch.com/docs/user-guide/profile-distributions)
- [Hermes plugins](https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins)
- [Hermes memory model](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory)
- [Hermes MCP support](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp)
- [ChatGPT developer mode and MCP availability](https://help.openai.com/en/articles/12584461-developer-mode-apps-and-full-mcp-connectors-in-chatgpt-beta)
- [OpenAI Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)
- [Official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [Claude MCP documentation](https://docs.anthropic.com/en/docs/mcp)
- [Cursor MCP documentation](https://docs.cursor.com/context/model-context-protocol)
- [Google Health API and Fitbit transition](https://developers.google.com/health/about)
- [Google Calendar push notifications](https://developers.google.com/workspace/calendar/api/guides/push)
- [Canvas LMS REST API](https://developerdocs.instructure.com/services/canvas)
- [GitHub Apps versus OAuth apps](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/differences-between-github-apps-and-oauth-apps)
- [Telegram Bot Platform](https://core.telegram.org/bots)
- [Slack Socket Mode](https://docs.slack.dev/apis/events-api/using-socket-mode/)
- [Ollama tool calling](https://docs.ollama.com/capabilities/tool-calling)
- [Ollama embeddings](https://docs.ollama.com/capabilities/embeddings)
- [Obsidian: sync notes across devices](https://obsidian.md/help/sync-notes)
- [Obsidian local and remote vaults](https://obsidian.md/help/Obsidian%2BSync/Local%2Band%2Bremote%2Bvaults)
- [Obsidian plugin Vault API](https://docs.obsidian.md/Plugins/Vault)
- [Obsidian community plugin submission and licensing](https://docs.obsidian.md/Plugins/Releasing/Submit%20your%20plugin)
- [Obsidian license and optional commercial services](https://obsidian.md/license)
