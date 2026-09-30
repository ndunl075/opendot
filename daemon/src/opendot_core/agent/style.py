"""Channel-independent reply style: persona rules a prompt cannot reliably hold."""

from __future__ import annotations

import re

#: Longest single message most chat channels accept. A long answer is truncated
#: rather than lost to a failed delivery.
MAX_BUBBLE_CHARS = 4096

#: An answer is sent as consecutive short messages rather than one block, the
#: way a person texts. Each paragraph becomes its own bubble, capped so a long
#: answer can't flood the chat.
DEFAULT_MAX_BUBBLES = 4

_TRUNCATION_NOTE = "\n\n[truncated]"

#: The persona forbids em/en dashes, and the model breaks that rule anyway. A
#: dash joining two clauses is exactly the long-sentence habit the persona is
#: trying to avoid, so it becomes a sentence break instead. Plain hyphens are
#: left alone: they are real punctuation inside words (fine-grained, re-run)
#: and in the "- item" lines allowed for short lists.
_CLAUSE_DASH = re.compile(r"\s*[—–]\s*")

#: Replies are sent as plain text, so markdown emphasis arrives as literal
#: asterisks: "**inbox**. 10 unread" is what the operator actually saw. The
#: persona already forbids markdown; this is the mechanical backstop.
_MARKDOWN_EMPHASIS = re.compile(r"(\*{1,3}|_{2,3})(?=\S)(.+?)(?<=\S)\1", re.DOTALL)
#: Leading "### " / "## " headings, which the model also reaches for.
_MARKDOWN_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)

#: A namespaced tool identifier as the model sees it, for example
#: ``mcp__opendot__availability_get``; the underscore-stripped
#: ``mcpopendotavailability_get`` is the same name after markdown emphasis has
#: eaten the ``__`` pairs. Both spellings are matched because either can
#: arrive depending on whether emphasis stripping ran first.
_TOOL_IDENTIFIER = re.compile(
    r"\bmcp(?:__)?(?:alfred|opendot)(?:__)?[a-z0-9_]+", re.IGNORECASE
)

#: A sentence, for removing the whole of one that names a tool. Splitting on
#: terminal punctuation rather than parsing: the text being repaired is one or
#: two chat sentences, not prose.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def enforce_style(text: str) -> str:
    """Apply the persona rules a prompt can't reliably hold.

    Dashes are the canonical case: the instruction not to use them is explicit
    and the model used one in its first live reply anyway. Rewriting a
    dash-joined clause as its own sentence is both the rule and the house
    style, so it is enforced here rather than re-asked for in the prompt.
    """
    replaced = _CLAUSE_DASH.sub(". ", text)
    # A dash right after sentence-ending punctuation would otherwise leave
    # ".. " or "?. " behind.
    replaced = re.sub(r"([.!?])\.\s+", r"\1 ", replaced)
    replaced = _MARKDOWN_HEADING.sub("", replaced)
    replaced = strip_tool_narration(replaced)
    replaced = _MARKDOWN_EMPHASIS.sub(r"\2", replaced)
    # Again after emphasis stripping: "mcp__opendot__x" only becomes the bare
    # "mcpopendotx" spelling once the underscore pairs are eaten, so a single
    # pass in either position misses one of the two forms.
    replaced = strip_tool_narration(replaced)
    return replaced


def strip_tool_narration(text: str) -> str:
    """Remove sentences that name an internal tool.

    A tool name in a reply is narration by definition -- the owner asked what
    their week looks like, not which function answers that. The whole sentence
    goes rather than the identifier alone, because deleting just the name
    leaves "it looks like is what i need". If every sentence names a tool
    there is nothing worth keeping, so the identifiers are dropped instead and
    whatever remains is returned -- an odd sentence is still better than an
    empty reply.

    Prompting handles the cause; this only guarantees the symptom cannot
    reach the owner, which a prompt alone never can.
    """
    if not _TOOL_IDENTIFIER.search(text):
        return text
    kept = [
        sentence
        for sentence in _SENTENCE_SPLIT.split(text)
        if sentence.strip() and not _TOOL_IDENTIFIER.search(sentence)
    ]
    if kept:
        return " ".join(kept)
    return re.sub(r"\s{2,}", " ", _TOOL_IDENTIFIER.sub("", text)).strip()


def split_into_bubbles(
    text: str, *, max_bubbles: int = DEFAULT_MAX_BUBBLES, limit: int = MAX_BUBBLE_CHARS
) -> list[str]:
    """Split an answer into consecutive chat messages on its blank lines.

    Paragraphs are the model's own unit of thought (one idea per paragraph),
    so they map to bubbles directly rather than this guessing at sentence
    boundaries. Anything past ``max_bubbles`` is folded back into the last
    bubble so nothing is silently dropped, and every bubble is individually
    truncated to ``limit``.
    """
    text = enforce_style(text)
    paragraphs = [block.strip() for block in text.split("\n\n") if block.strip()]
    if not paragraphs:
        return [truncate(text.strip() or "(no answer)", limit=limit)]
    if len(paragraphs) > max_bubbles:
        head = paragraphs[: max_bubbles - 1]
        tail = "\n\n".join(paragraphs[max_bubbles - 1 :])
        paragraphs = head + [tail]
    return [truncate(paragraph, limit=limit) for paragraph in paragraphs]


def truncate(text: str, *, limit: int = MAX_BUBBLE_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - len(_TRUNCATION_NOTE)] + _TRUNCATION_NOTE
