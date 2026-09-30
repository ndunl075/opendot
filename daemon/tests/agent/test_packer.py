from __future__ import annotations

from opendot_core.agent.packer import (
    SUMMARY_MARKER,
    PromptPacker,
    Tail,
    assert_prefix_stable,
    compact,
    estimate_tokens,
    needs_compaction,
    trim_tool_result,
)
from opendot_core.models import Redactor
from opendot_core.providers.types import InputItem, ToolSpec

RULES = "Be brief."
PERSONA = "You are Dot."
TOOLS = [ToolSpec(name="b_tool", description="b"), ToolSpec(name="a_tool", description="a")]


def pack(packer=None, *, persona=PERSONA, rules=RULES, tools=TOOLS, summary="", history=(), tail=None):
    packer = packer or PromptPacker()
    return packer.pack(
        persona=persona,
        rules_text=rules,
        tools=tools,
        summary=summary,
        history=list(history),
        tail=tail or Tail(message="hi"),
    )


def test_section_order() -> None:
    history = [InputItem(role="user", content="old"), InputItem(role="assistant", content="reply")]
    p = pack(summary="SUMMARY", history=history, tail=Tail(message="NEWMSG", now="NOW", memories=["MEM"]))
    text = p.instructions
    assert text.index(RULES) < text.index(PERSONA) < text.index("a_tool") < text.index("SUMMARY")
    assert p.input[:2] == history
    last = p.input[-1]
    assert last.role == "user"
    assert last.content.index("NOW") < last.content.index("MEM") < last.content.index("NEWMSG")


def test_prefix_hash_stable_across_tails_histories_times() -> None:
    a = pack(tail=Tail(message="one", now="10:00", memories=["x"]))
    b = pack(
        history=[InputItem(role="user", content="q")],
        summary="s",
        tail=Tail(message="two", now="11:00", memories=["y", "z"]),
    )
    assert a.prefix_hash == b.prefix_hash
    assert a.instructions.split(SUMMARY_MARKER)[0] == b.instructions.split(SUMMARY_MARKER)[0]


def test_prefix_changes_with_persona_rules_tools() -> None:
    base = pack().prefix_hash
    assert pack(persona="Other").prefix_hash != base
    assert pack(rules="Be long.").prefix_hash != base
    assert pack(tools=[*TOOLS, ToolSpec(name="c_tool")]).prefix_hash != base


def test_tool_schemas_sorted_regardless_of_input_order() -> None:
    a = pack(tools=[ToolSpec(name="z"), ToolSpec(name="a"), ToolSpec(name="m")])
    b = pack(tools=[ToolSpec(name="m"), ToolSpec(name="z"), ToolSpec(name="a")])
    assert a.prefix_hash == b.prefix_hash
    assert [t.name for t in a.tools] == ["a", "m", "z"]
    assert a.instructions.index('"a"') < a.instructions.index('"m"') < a.instructions.index('"z"')


def test_budgets_enforced_and_message_untouched() -> None:
    packer = PromptPacker()
    memories = [f"memory {i} " + "m" * 300 for i in range(40)]
    tasks = [f"task {i} " + "t" * 200 for i in range(40)]
    message = "keep this message " + "q" * 5000
    tail = Tail(message=message, memories=memories, tasks=tasks, profile="p" * 5000, now="now")
    p = pack(packer, persona="x" * 5000, summary="s" * 5000, tail=tail)
    content = p.input[-1].content
    assert content.endswith(message)
    context = content[: -len(message)]
    assert estimate_tokens(context) <= 350 + 400 + 900 + 100
    assert "memory 0 " in content and "memory 39 " not in content  # lowest value dropped first
    assert "task 0 " in content and "task 39 " not in content
    summary = p.instructions.split(SUMMARY_MARKER)[1]
    assert estimate_tokens(summary) <= 700
    assert "[truncated]" in summary
    persona = p.instructions.split("## Persona\n")[1].split("\n\n## Tools")[0]
    assert estimate_tokens(persona) <= 250


def test_redactor_scrubs_memories_not_message() -> None:
    tail = Tail(message="mail mom@example.com", memories=["boss is bob@corp.com"], redactor=Redactor())
    content = pack(tail=tail).input[-1].content
    assert "bob@corp.com" not in content
    assert "mom@example.com" in content


def test_trim_html() -> None:
    page = "<html><head><title>t</title><style>p{}</style></head><body><script>evil()</script><p>Hello &amp; welcome</p><div>Line two</div></body></html>"
    out = trim_tool_result(page)
    assert out == "Hello & welcome\n\nLine two"
    long = trim_tool_result("<p>" + "word " * 2000 + "</p>", 100)
    assert "[truncated]" in long
    assert len(long) < 200


def test_trim_email_dict() -> None:
    email = {
        "id": "1",
        "from": "a@x.com",
        "to": "b@x.com",
        "subject": "Hi",
        "date": "today",
        "body": "<div>" + "long body " * 1000 + "</div>",
        "attachments": ["big"] * 50,
    }
    out = trim_tool_result(email, 1000)
    assert "a@x.com" in out and "Hi" in out and "snippet" in out
    assert "attachments" not in out
    assert len(out) <= 1100


def test_compaction_threshold_and_append_only() -> None:
    history = [
        InputItem(role="user" if i % 2 == 0 else "assistant", content=f"turn {i} " + "w" * 90) for i in range(20)
    ]
    assert not needs_compaction(history, 10_000)
    assert needs_compaction(history, 100)
    snapshot = [i.model_copy() for i in history]
    calls: list[str] = []

    def summarize(text: str) -> str:
        calls.append(text)
        return "SUM"

    summary, kept = compact(history, summarize, keep_recent=4)
    assert summary == "SUM" and len(calls) == 1
    assert kept == snapshot[-4:]
    assert history == snapshot  # input untouched
    assert "turn 0 " in calls[0] and "turn 19 " not in calls[0]


def test_compaction_keeps_tool_result_with_call() -> None:
    history = [
        InputItem(role="user", content="u"),
        InputItem(role="assistant", content="", tool_call_id="c1", tool_name="t", tool_arguments="{}"),
        InputItem(role="tool", content="result", tool_call_id="c1"),
        InputItem(role="assistant", content="done"),
    ]
    _, kept = compact(history, lambda t: "S", keep_recent=2)
    assert kept[0].role == "assistant" and kept[0].tool_call_id == "c1"
    summary, same = compact(history[:2], lambda t: "S", keep_recent=6)
    assert same == history[:2]
    assert summary == ""


def test_deterministic_and_prefix_stable_helper() -> None:
    a = pack(tail=Tail(message="m", now="t", memories=["a", "b"]))
    b = pack(tail=Tail(message="m", now="t", memories=["a", "b"]))
    assert a == b
    r1 = pack(tail=Tail(message="1", now="1")).to_request("m")
    r2 = pack(summary="new", history=[InputItem(role="user", content="h")], tail=Tail(message="2")).to_request("m")
    assert assert_prefix_stable([r1, r2])
    r3 = pack(persona="different").to_request("m")
    assert not assert_prefix_stable([r1, r3])
    assert not assert_prefix_stable([r1, pack(tools=TOOLS[:1]).to_request("m")])
