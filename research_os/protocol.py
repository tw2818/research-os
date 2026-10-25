"""The protocol block injected into the user's message while in research mode.

Design constraints this file exists to satisfy:

1. The user never sees this text. It rides the pre_llm_call context channel,
   which Hermes appends to the user message as agent-facing context.
2. It must stay small. Every injected character breaks the prompt cache for
   the rest of the turn, so the block is capped by config `max_inject_chars`.
3. It must not turn the agent into a lecturer. A chore request inside a
   research session must still get a chore answer.
4. It must separate what a source reports from what the agent concludes.
"""

from __future__ import annotations

import re
from typing import Any

try:
    # Private namespace first: the plugin loads its modules under
    # `research_os_impl.*` so they cannot collide with another plugin's
    # `state.py` and cannot go stale behind a reloaded entry module.
    from research_os_impl.state import read_claims, read_contradictions, read_map
except ImportError:
    try:
        from .state import read_claims, read_contradictions, read_map
    except ImportError:  # loaded flat inside the Hermes plugin directory
        from state import read_claims, read_contradictions, read_map  # type: ignore[no-redef]

# The four claim types, stated as the agent's own working vocabulary.
# The claim discipline. Not injected any more: the full format is in the
# tool's own description, and an agent that is about to write a claim reads
# the tool schema anyway. Restating it on every turn cost 70% of the
# injection budget to repeat something the agent was told anyway, and the
# budget is worth more spent on how to think.
#
# Kept here because the tool description and this must not drift apart, and
# because the reference implementation uses it in its own error messages.
CLAIM_RULES = """记进账本时用 research_mode(action='claim', project='<课题>', claim={...})：
observation 事实(要文献)｜ report 转述(要文献)｜ inference 推论(premises 填行号，强度不超最弱前提)｜ hypothesis 假设(无来源但要留作假设)。
没来源的别写成事实。"""

# How to think about the question, before answering it.
#
# This is the part that makes the mode research rather than bookkeeping.
# It comes from the same place the multi-perspective review rules come from,
# and it is deliberately generative: each move produces something new rather
# than checking that something old is well-formed.
#
# The moves, in the order a researcher actually makes them:
#   1. reframe the question - the asked question is often a symptom
#   2. generate rival explanations, including one that contradicts the
#      obvious reading
#   3. ask what observation would tell them apart
#   4. look for the trap - the inference that looks available but is not
#   5. say plainly what is not known
#
# The fourth move is the one models skip. "Remission still shows a deficit,
# therefore the circuit is damaged" is the inference that looks available and
# is not, because a residual state explains it too. A mode that does not
# name that move will let the agent make it.
THINKING_RULES = """回答前按科研的路子走：
1. 问准：这问题背后真正要答的是什么？现在问的可能是它的影子。
2. 给竞争解释：至少两种，包括跟你直觉相反的那种。写清各自预测什么。
3. 找抓手：什么观察能把它们分开？分不开就直说分不开，别糊过去。
4. 找陷阱：哪个推论看着成立其实不成立？（如"缓解后仍有缺损 ⇒ 环路已损伤"——残留状态也解释得通）
5. 标边界：哪里还不确定、哪里只是推测，说出来。

不确定就写不确定。允许答案是"现在还不能定"。"""

# What the turn has to produce. A research session that ends with the same
# state it started in has done nothing, and nothing in the protocol can
# detect that except asking for it.
#
# The list is ordered by what a researcher would actually do, and the
# bookkeeping comes last on purpose: a turn that only records has done
# administration, not research.
TURN_DEMAND = """这轮要有新东西，别只复述已有内容。至少做到一件（按科研价值排序）：
提出或改写一个竞争解释｜说出区分两个解释的观察｜指出一个看着成立其实不成立的推论｜
关掉一个待解问题｜明确说清这里无法定论｜新增一条带来源的断言。
做完整之后用 research_mode 记下来，例如 research_mode(action='claim', project='<课题>', claim={...})
（提解释用 action='contradict'，关问题用 action='question'，提假设用 action='hypothesis'）。"""

SCOPE_RULE = """如果用户这轮要的是操作（转格式、导入、装东西、改代码），直接做，不要讨论研究意义。
只有用户问研究问题时才按上面的四类断言来写。"""


def _claims_digest(topic: str, limit: int = 6) -> str:
    """A short view of the ledger: how many claims, and what is still pending."""
    claims = read_claims(topic)
    if not claims:
        return "账本：空"
    by_kind: dict[str, int] = {}
    pending: list[str] = []
    for c in claims:
        k = c.get("kind", "?")
        by_kind[k] = by_kind.get(k, 0) + 1
        if (c.get("status") or "") not in ("verified", "derived", "established"):
            pending.append(c.get("id", "?"))
    bits = ", ".join(f"{k}×{v}" for k, v in sorted(by_kind.items()))
    tail = f"；待核 {len(pending)} 条" if pending else "；无待核"
    return f"账本：{len(claims)} 条（{bits}）{tail}"


def _situation(topic: str) -> list[str]:
    """Where the research stands right now.

    The point of a research session is not to be tidy about claims - it is to
    move the question forward. So the injection says what the state of play
    is: which explanation currently looks best, which one is under pressure,
    and what the cheapest next test would be. That is what a researcher
    wants to know when they sit down, and it is what an agent needs in order
    to do research instead of summarising.
    """
    data = read_contradictions(topic) or {}
    items = [i for i in (data.get("items") or []) if isinstance(i, dict)]
    if not items:
        return []
    lines: list[str] = []
    best = [i for i in items if str(i.get("status") or "").lower() in ("best", "leading")]
    live = [i for i in items if str(i.get("status") or "").lower() not in
            ("refuted", "rejected", "killed", "retired")]
    if best:
        lines.append("当前最佳解释：" + "；".join(str(i.get("title") or "") for i in best[:2]))
    elif live:
        lines.append("候选解释：" + "；".join(str(i.get("title") or "") for i in live[:3]))
    # The explanation under pressure is the one whose separator nobody has
    # run yet, or whose status says it is contested.
    pressured = [i for i in items if str(i.get("status") or "").lower() in
                 ("contested", "weakening", "challenged")]
    for i in pressured[:2]:
        lines.append(f"受质疑的解释：{i.get('title')} —— 分开它需要：{i.get('separator')}")
    # The cheapest next test is the separator of an explanation that is still
    # live: it says what evidence would move the question, and moving the
    # question is the work.
    for i in live[:2]:
        sep = str(i.get("separator") or "").strip()
        if sep:
            lines.append(f"能分开它的证据：{sep}")
            break
    return lines


def build_research_block(topic: str, config: dict[str, Any] | None = None) -> str:
    """The text injected while a session is in research mode for `topic`."""
    m = read_map(topic)
    if not m:
        return f"[Research OS · {topic}]\n该课题还没有 research-map.json，先读它再回答。"

    hyp = [h for h in (m.get("hypotheses") or []) if str(h).strip()]
    open_q = [q for q in (m.get("open_questions") or []) if str(q).strip()]

    lines = [f"[Research OS · {topic}]"]
    lines.append(f"核心问题：{m.get('core_question', '')}")
    # The situation comes first: what the state of play is, not a list of
    # things someone wrote down once.
    lines.extend(_situation(topic))
    if not any(
        ln.startswith(("当前最佳解释", "候选解释", "受质疑", "能分开")) for ln in lines
    ) and hyp:
        lines.append("竞争解释：" + " ｜ ".join(hyp))
    if open_q:
        lines.append("待解问题：" + " ｜ ".join(open_q[:2]))
    # The thinking rules come before the claim rules. A mode that leads with
    # the format teaches the model to fill the format; a mode that leads with
    # the thinking teaches it to think, and the ledger is where the thinking
    # gets recorded.
    lines.append(THINKING_RULES)
    lines.append(TURN_DEMAND)
    lines.append(SCOPE_RULE)
    lines.append(f"（以上仅属于 {topic} 课题，不要套用到别的话题。）")

    block = "\n".join(lines)
    cap = int((config or {}).get("max_inject_chars", 900))
    if len(block) > cap:
        block = _truncate(block, cap)
    return block


def _truncate(block: str, cap: int) -> str:
    """Cut the block to fit, keeping the parts that act rather than the parts
    that are merely long.

    Two things are cut differently. A single long line - a core question
    typed by a person - is truncated, because there is nothing else to do
    with it. A rule that spans several lines is kept whole, because half a
    list of moves is worse than no list: the agent reads the heading of a
    set and none of the set.

    The order is: the three rule blocks first (they are what changes the
    agent's behaviour), then the situational lines (what state the topic is
    in), then whatever fits.
    """
    if len(block) <= cap:
        return block

    def clip(line: str) -> str:
        # A long situational line yields to the rules. The question can be
        # read in full in the topic file; the rules are what the agent needs
        # in front of it.
        limit = max(80, min(200, cap // 6))
        return line if len(line) <= limit else line[: limit - 1] + "…"

    blocks = [THINKING_RULES, TURN_DEMAND]
    # A tiny line that survives every cut: naming the tool call. Without it
    # an agent told to "record it" writes into prose, and the ledger stays
    # empty while the answer looks complete.
    tool_line = "做完整之后用 research_mode(action='claim', project='<课题>', claim={...}) 记下来。"
    # The situational lines come first: they are what the turn is about, and
    # the rules are how to work on it. Losing the situation to keep a rule
    # inverts the block.
    priority = re.compile(
        r"^(\[Research OS|核心问题：|当前最佳解释|候选解释|受质疑的解释|"
        r"能分开它的证据|竞争解释：|待解问题：|如果用户这轮|（以上仅属于)"
    )
    kept: list[str] = []
    used = 0
    for line in block.split("\n"):
        if any(line.startswith(b.split("\n")[0][:10]) for b in blocks):
            continue
        if not priority.match(line):
            continue
        line = clip(line)
        cost = len(line) + 1
        if used + cost > cap:
            continue
        kept.append(line)
        used += cost

    for b in blocks:
        cost = len(b) + 1
        if used + cost > cap:
            continue
        kept.append(b)
        used += cost
    if not kept:
        kept.append(clip(blocks[0]))
    else:
        line = clip(tool_line)
        cost = len(line) + 1
        if used + cost <= cap:
            kept.append(line)
        else:
            # Make room by dropping the least important block first.
            for i in range(len(kept) - 1, -1, -1):
                if kept[i] in blocks and kept[i] is not blocks[0]:
                    freed = len(kept[i]) + 1
                    del kept[i]
                    used -= freed
                    if used + cost <= cap:
                        kept.append(line)
                        break

    out = "\n".join(kept)
    if len(out) > cap:
        out = out[: cap - 1] + "…"
    return out


def build_idle_block() -> str:
    """Injected in idle mode: almost nothing. Just enough that the agent knows
    the capability exists if a research question shows up mid-chore."""
    return (
        "[Research OS · idle]\n"
        "当前不在科研模式（无需四类断言纪律）。"
        "若用户这轮是研究问题（求解释、比较、查证、假设、证据），"
        "先调 research_mode(action='on', project=...) 进入对应课题，再按四类断言回答。"
    )
