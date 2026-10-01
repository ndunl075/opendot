# M2 design: module contracts

Shared contract for the M2 work (ARCHITECTURE.md section 14, tasks 2.1 to 2.9). Every agent builds one piece against these names so the pieces fit. Where this file and ARCHITECTURE.md disagree, ARCHITECTURE.md wins. Package root: `daemon/src/opendot_core/`.

## Migrations (forward-only, one number per piece)

- 0019 `usage` (credit records, budgets)
- 0020 `rules` (rules table)
- 0021 `agent_loop` (agent tasks and steps)

## `usage/` (task 2.5)

- `usage/rate_card.toml`: credits per million tokens (input, cached_input, output) per model family, from section 8.1 (sol 125/12.5/750, terra 50/5/300, luna 5/0.5/30). Matching is by family word in the model id. Unknown models: "rate unknown", charged at the top-tier (sol) rate.
- `usage/meter.py`:
  - `load_rate_card(path=None) -> RateCard`; `RateCard.credits(model: str, usage: Usage) -> CreditCost(credits: float, rate_known: bool)`.
  - `UsageMeter(database, *, budgets: Budgets | None = None, clock=...)`.
  - `record(*, task_id: str, job_type: str, model: str, effort: str | None, usage: Usage) -> CreditRecord` (stores per call; totals per step/task/day/job type are derived).
  - `credits_for_task(task_id) -> float`, `credits_today() -> float` (day = UTC date), `summary(days=7) -> UsageSummary` (by task, day, job type).
  - `Budgets(task_credits=5.0, daily_credits=50.0)`, persisted and user-changeable: `get_budgets()`, `set_budgets(...)`.
  - `check_before_request(task_id: str) -> None`: raises `DailyBudgetExceeded` (hard stop, no override except raising the budget) or `TaskBudgetExceeded` (pausable; `allow_task_overrun(task_id)` records the user's "continue anyway" for that task).
  - Errors live in `usage/errors.py`, both subclass `BudgetExceeded`.
- Default budgets are 5 credits per task and 50 per day.

## `rules/` (task 2.6)

- `Behavior` (StrEnum): `auto`, `auto_if_preapproved`, `ask`, `handoff`.
- `ToolIntent(tool: str, action: str, target: str | None, sensitivity: str, cost_credits: float = 0.0, user_preapproved: bool = False)`; `user_preapproved` is True only when the user's own message explicitly asked for this exact action.
- `Rule(id, tool, action, target, max_sensitivity, behavior, created_by, created_at, note)`; `tool`/`action`/`target` accept `*` globs.
- `RuleEngine(database)`: `decide(intent) -> Decision(behavior, rule_id | None, reason, locked: bool)`; `add_rule`, `update_rule`, `delete_rule`, `list_rules()`; `always_allow(approval_id, actor) -> Rule` (creates an `auto` rule from an approved proposal's tool/action/target, never for a deny-list intent).
- Defaults (section 10): reads and syncing `auto`; saving messages, low-risk memory, local tasks and reminders `auto`; creating Calendar events, Gmail drafts, GitHub issues, file changes `ask`; sending or posting `ask` every time; everything unmatched `ask`.
- Core deny list (`rules/deny_list.py`): deleting data in outside apps, spending money, security, credential and password changes, all `handoff`, `locked=True`, and NO rule (including an `auto` rule or `always_allow`) can change that. `list_rules()` returns the deny-list items as locked entries.
- Most specific rule wins; ties go to the stricter behavior (`handoff` > `ask` > `auto_if_preapproved` > `auto`).

## `router/` (task 2.4)

- `Tier` (StrEnum): `cheap`, `mid`, `top`. `Job` (StrEnum): `sort`, `summarize`, `chat`, `plan`, `tool_step`, `review`, `deep`.
- `Router(catalog: list[ModelInfo], overrides: dict[Tier, str] | None = None, auto_top: bool = False)`.
- Tier discovery (section 7.2): family word `luna` -> cheap, `terra` -> mid, `sol` -> top; a missing tier uses the next tier UP; Astra is never routed to automatically; `overrides` win.
- `route(job, *, failed_at: Tier | None = None, effort_bump: bool = False) -> Route(model, effort, tier, needs_approval)` following the section 7.1 table: sort/summarize/chat -> cheap/low; plan and tool_step -> cheap/medium; review -> mid/medium; deep -> top/high; after a failure move up exactly one tier (never skipping, at most once per step) with the same effort or one step higher; runtime never uses `xhigh` or above; a `top` route has `needs_approval=True` unless `auto_top`; if the top tier just failed raise `HandOff` (hand the task to the user).

## `agent/packer.py` (task 2.3)

- Prompt layout in this exact order (section 8.2): stable prefix (rules, persona, the tool group's schemas sorted by name), task summary, append-only history, changing tail (current time, retrieved memories, the new message).
- `PromptPacker.pack(*, persona, rules_text, tools: list[ToolSpec], summary: str, history: list[InputItem], tail: Tail) -> PackedPrompt(instructions, input, prefix_hash)`; `prefix_hash` covers only the stable prefix and is byte-identical for the same persona/rules/tool group.
- Budgets (section 8.2 rule 7): about 3,000 tokens of extra context per call (persona 250, profile 350, tasks 400, memories 900, summary 700, reserve 400); `trim_tool_result(text, limit)` (extracted text, capped lengths).
- Compaction: `needs_compaction(history, threshold_tokens)`; `compact(history, summarize: Callable[[str], str]) -> (new_summary, kept_history)` called once per threshold crossing with the cheap model; history is never edited or reordered otherwise.

## `agent/loop.py` and `agent/loop_api.py` (tasks 2.2, 2.7)

- `AgentLoop(database, registry, router, meter, rules, packer, tools, reviewer, ...)` implements the protocol in `agent/loop_api.py`.
- Durable: every step is written to `agent_steps` before and after it runs, so `resume_all()` after a restart continues each unfinished task from its last completed step without repeating a finished tool call.
- Tool group chosen once per task (`agent/tool_groups.py`, at most 8 tools) and never changed mid-task.
- Every tool call is decided by `RuleEngine` (`auto` runs, `ask` creates a proposal and the task waits, `handoff` refuses and tells the user what to do, `auto_if_preapproved` per the intent flag); approvals use the existing `ApprovalService`; the model can never approve its own proposal.
- Before each model request: `UsageMeter.check_before_request`; after `Completed`: `UsageMeter.record`.
- Errors never switch provider: `UsageLimitExceeded` (429) pauses every plan request (task state `paused_limit`).
- Reviewer (`agent/reviewer.py`): `Reviewer.review(proposal) -> ReviewNote(verdict: ok|concern|block, reasons)`; deterministic checks first, then a mid-tier model pass; anomaly auto-pause (see the agent 2.7 brief).

## `api/server.py` (task 2.8)

Starlette app on 127.0.0.1 with bearer token implementing the `contract/` endpoints needed for chat and approvals, plus the `/v1/chat/stream` WebSocket, backed by `AgentLoop`.

## `eval/` (task 2.1)

`opendot eval --suite core` runs scenarios S1 to S10 (ARCHITECTURE.md section 14, M2) with a scripted fake model provider, prints one `PASS Sn ...` or `FAIL Sn ...` line each, and exits non-zero unless all pass.
