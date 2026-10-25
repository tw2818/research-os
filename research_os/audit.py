"""Detecting unaccounted reasoning in a turn.

The ledger exists so that what the agent *says* is traceable. Two things can
go wrong and neither is visible in a unit test:

1. The agent retrieved literature and then wrote the answer without recording
   anything. The evidence lives in the chat and nowhere else.
2. The agent made a claim in prose that reads like a fact but was never
   classified, so its type was never checked.

Both are detected from the conversation itself, before the agent writes its
final answer — which is the only moment a correction still costs nothing.
"""

from __future__ import annotations

import re
from typing import Any

# Tools whose results are evidence. A turn that used one of these and left the
# ledger empty has done research it cannot account for.
EVIDENCE_TOOLS = (
    "mcp__pubmed__pubmed_search_articles",
    "mcp__pubmed__pubmed_fetch_articles",
    "mcp__pubmed__pubmed_fetch_fulltext",
    "mcp__pubmed__pubmed_europepmc_search",
    "mcp__pubmed__pubmed_europepmc_fetch",
    "mcp__pubmed__pubmed_find_related",
    "mcp__pubmed_search_mcp__unified_search",
    "mcp__pubmed_search_mcp__get_fulltext",
    "mcp__pubmed_search_mcp__get_article_references",
    "mcp__pubmed_search_mcp__find_citing_articles",
    "mcp__pubmed_search_mcp__fetch_article_details",
    "mcp__openalex__openalex_search_entities",
    "mcp__openalex__openalex_get_citation_graph",
    "mcp__openalex__openalex_resolve_name",
    "mcp__zotero__zotero_item_find_by_dois",
    "web_search",
)

# The agent's own recording tool. Its presence means the agent did account for
# something.
_LEDGER_TOOL = "research_mode"

# A PMID, DOI, or a bracketed numeric reference. Used to count how much
# distinct source material the turn touched.
_CITATION = re.compile(
    r"(?:\bPMID[:\s]*(\d{6,9})\b)"
    r"|(?:\bdoi[:\s]*(10\.\d{4,9}/[^\s\"'<>,;]+))"
    r"|(?:\[\s*(\d{1,3})\s*\])",
    re.IGNORECASE,
)

# Sentences that assert rather than report. A research answer should not be
# built out of these unless a verified claim backs them.
_ASSERTIVE = re.compile(
    r"(已经证实|已证实|证实了|证明了|表明|说明|意味着|提示|"
    r"is confirmed|is proven|shows that|demonstrates that|indicates that|"
    r"it is known that|it has been shown)",
    re.IGNORECASE,
)

# Hedged sentences are fine — they are how a hypothesis is written.
_HEDGED = re.compile(
    r"(可能|或许|推测|似乎|尚未|不确定|待验证|有待|倾向于认为|"
    r"may |might |suggests? that it|possibly|hypothes|unclear|remains)",
    re.IGNORECASE,
)

# A clause boundary, used to split a long answer into sentences.
_CLAUSE = re.compile(r"[。；;！!？?\n]|(?<=[a-z0-9])\.\s")


def _tool_name_of(msg: Any) -> str:
    if not isinstance(msg, dict):
        return ""
    name = msg.get("tool_name") or msg.get("name") or ""
    return str(name)


def _content_of(msg: Any) -> str:
    if not isinstance(msg, dict):
        return ""
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "\n".join(parts)
    return ""


def scan_turn(history: list[Any]) -> dict[str, Any]:
    """Describe what the turn did with evidence.

    Returns how many evidence tools it used, how many distinct citations the
    tool output carried, and whether the agent recorded anything in the ledger.
    """
    evidence_calls = 0
    ledger_calls = 0
    citations: set[str] = set()
    for msg in history or []:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "tool":
            continue
        name = _tool_name_of(msg)
        if name in EVIDENCE_TOOLS:
            evidence_calls += 1
            text = _content_of(msg)
            for m in _CITATION.finditer(text):
                for group in m.groups():
                    if group:
                        citations.add(group)
        elif name == _LEDGER_TOOL:
            ledger_calls += 1
    return {
        "evidence_calls": evidence_calls,
        "ledger_calls": ledger_calls,
        "citations": len(citations),
        "citation_ids": sorted(citations),
    }


def split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _CLAUSE.split(text or "")]
    return [p for p in parts if len(p) > 12]


def assertive_sentences(text: str) -> list[str]:
    """Sentences that assert rather than report.

    A sentence that hedges is not a problem — it is how a hypothesis or a
    tentative reading is written. Only unhedged assertions are candidates.
    """
    out = []
    for sentence in split_sentences(text):
        if _ASSERTIVE.search(sentence) and not _HEDGED.search(sentence):
            out.append(sentence)
    return out


def unaccounted_sentences(text: str, known: str) -> list[str]:
    """Assertive sentences that do not appear in the ledger.

    `known` is the concatenated ledger content. A sentence that is asserted
    but nowhere in the ledger is a claim the agent made without recording it.
    """
    if not known.strip():
        return assertive_sentences(text)
    known_low = known.lower()
    out = []
    for sentence in assertive_sentences(text):
        # A sentence is accounted for if a decent chunk of it is in the ledger.
        probe = sentence[:40].lower()
        if probe and probe in known_low:
            continue
        out.append(sentence)
    return out

# Markers of a position: the answer says what it thinks, not only what it
# found. A research answer without one of these is a list of findings, and a
# list of findings is what the reader cannot use.
_POSITION_MARKERS = (
    "我认为", "我觉得", "我倾向", "目前看", "现阶段", "结论是", "综合来看",
    "最可能", "更可能", "更支持", "不支持", "可以认为", "提示", "说明",
    "因此", "所以", "这意味着", "据此", "初步判断", "倾向于认为",
    "the evidence suggests", "these findings suggest", "taken together",
    "overall", "our best", "points to", "argues for",
)

# Markers of a hedge with no position behind it: the sentence gives the
# reader nothing to hold.
_HEDGE_ONLY = (
    "有待进一步研究", "需要更多研究", "尚需进一步", "未来需要",
    "仍需进一步验证", "有待验证", "尚不清楚", "有待进一步确认",
)


def has_position(text: str) -> bool:
    """True when the text states what it thinks, not only what it found.

    This is the check that matters for the shape of an answer. A model that
    gathers evidence reliably and never says what it makes of it produces a
    report: every point hedged, every caveat listed, no position anywhere.
    The reader is left to do the synthesis, which is the part that needed
    doing.
    """
    if not text or not text.strip():
        return False
    low = text.lower()
    if any(m in low for m in _POSITION_MARKERS):
        return True
    # A bare list of findings with hedges is not a position.
    hedges = sum(1 for m in _HEDGE_ONLY if m in text)
    return hedges > 0 and False
