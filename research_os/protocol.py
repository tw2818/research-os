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
    from research_os_impl.state import read_claims, read_contradictions, read_map, read_review
except ImportError:
    try:
        from .state import read_claims, read_contradictions, read_map, read_review
    except ImportError:  # loaded flat inside the Hermes plugin directory
        from state import read_claims, read_contradictions, read_map, read_review  # type: ignore[no-redef]

# The phases of a research project, and what thinking each one calls for.
#
# A project moves through these, and the way of thinking changes with them.
# Scoping a question, gathering evidence, weighing rival explanations, and
# writing up are different jobs, and an agent that uses the same method for
# all four will gather evidence while it should be deciding what to ask, or
# write while it should be arguing with itself.
#
# The phase is the agent's own: it moves the phase when the work moves, and
# the switch is a tool call, not a guess made by the hook.
PHASES: dict[str, dict[str, str]] = {
    "scoping": {
        "name": "界定问题",
        "thinking": (
            "这轮在界定问题，别急着找证据。要回答的科学问题是什么？"
            "为什么这个答案会有用？有哪些可能的问题问法？选哪个，代价是什么？"
            "什么答案会让你改变问法？"
        ),
        "output": (
            "给出你选定的问法，以及放弃其他问法的代价。"
        ),
        "gate": (
            "这一阶段不要急着找证据。先把问题问准——"
            "如果用户这轮给的是一个具体任务，先问它服务的是哪个问法。"
        ),
    },
    "evidence": {
        "name": "搜集证据",
        "thinking": (
            "这轮在搜集证据。带着具体问题去找，别漫无目的地捞文献："
            "要找的证据能支持或否定哪个说法？找之前先说清你要看到什么。"
            "不同来源的结论冲突时，先想清楚它们测的是不是同一件事。"
        ),
        "output": (
            "给出这批证据支持什么、不支持什么，一句话结论。"
        ),
        "gate": (
            "这一阶段只找证据，不下结论，也不改问题。"
            "找之前先说清你要看到什么——"
            "如果用户这轮要的是一个判断，说明现在还不到回答的时候。"
        ),
    },
    "weighing": {
        "name": "权衡解释",
        "thinking": (
            "这轮在权衡解释。看哪个预测被证实、哪个被削掉。"
            "注意：证据不支持某个解释，和证据支持另一个解释，是两回事——"
            "后者需要正面证据。如果两个解释都能解释同一批数据，那说明还缺一个观察。"
        ),
        "output": (
            "给出哪个解释现在更划算、哪个被削掉了、依据是什么。"
        ),
        "gate": (
            "这一阶段做取舍：不必把所有材料都摆出来，"
            "挑最能改变判断的那几条。"
        ),
    },
    "closing": {
        "name": "收束结论",
        "thinking": (
            "这轮在收束。结论能说到多强？逐条检查依据够不够。"
            "哪里是推测，哪里是事实，哪里连方向都不确定——分开写。"
            "什么新证据会推翻这个结论？如果没有，那这个结论就不牢靠。"
        ),
        "output": (
            "给出结论、它的强度、以及在什么条件下不成立。"
        ),
        "gate": (
            "这一阶段不要新增证据。结论要改，先说清哪条证据会改它。"
        ),
    },
    "writing": {
        "name": "成文表达",
        "thinking": (
            "这轮在写。先说清楚给谁看、他们已经知道什么、看完要知道什么。"
            "少用'研究表明'，直接说是谁、什么设计、什么效应量、什么限制。"
        ),
        "output": (
            "给出成稿的段落式结论，读者读完能带走一个立场。"
        ),
        "gate": (
            "这一阶段只写。发现结论有问题就标出来，别在写作时顺手改结论。"
        ),
    },
}

DEFAULT_PHASE = "weighing"


def phase_thinking(phase: str) -> str:
    """The thinking instruction for a phase, with its name."""
    entry = PHASES.get(str(phase or "").strip())
    if not entry:
        entry = PHASES[DEFAULT_PHASE]
    return f"当前阶段：{entry['name']}\n{entry['thinking']}"


def phase_gate(phase: str) -> str:
    """What this phase is not for.

    This is what replaced a keyword matcher that tried to guess whether a
    request fitted the phase. The matcher fired on "再查" and missed "再
    找找", and it could not tell a research search from a technical one -
    but the agent knows which is which, and can say so. Stating the
    boundary in the phase is more reliable than guessing at it from
    outside.
    """
    entry = PHASES.get(str(phase or "").strip()) or PHASES[DEFAULT_PHASE]
    return str(entry.get("gate") or "")


def phase_output(phase: str) -> str:
    """The shape the answer should take in this phase.

    The phase has to change the shape of the output, not only the way of
    thinking about it. A model that gathers evidence in the weighing phase
    will produce a list of findings with no position in it, and a list of
    findings is what the reader cannot use.
    """
    entry = PHASES.get(str(phase or "").strip()) or PHASES[DEFAULT_PHASE]
    return str(entry.get("output") or "")


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
5. 取舍：这一轮只能推进一点，说清推进了哪一点、剩下的为什么先放着。
   不要把所有相关材料都摆出来——摆全了读者拿不到结论。
6. 标边界：哪里还不确定、哪里只是推测，说出来。

不确定就写不确定。允许答案是"现在还不能定"。

先给判断，再给依据，最后给还缺什么。不要写成条目清单——清单把判断淹了。"""

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
一次查出几条就一次记完：claims=[{...},{...}]，不用一条一个调用。\n（提解释用 action='contradict'，关问题用 action='question'，提假设用 action='hypothesis'）。"""

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
        lines.append("当前最佳解释：" + "；".join(_short(i.get("title")) for i in best[:1]))
    elif live:
        lines.append("候选解释：" + "；".join(_short(i.get("title")) for i in live[:2]))
    # The explanation under pressure is the one whose separator nobody has
    # run yet, or whose status says it is contested.
    pressured = [i for i in items if str(i.get("status") or "").lower() in
                 ("contested", "weakening", "challenged")]
    for i in pressured[:1]:
        lines.append(f"受质疑的解释：{_short(i.get('title'))} —— 分开它需要：{_short(i.get('separator'))}")
    # The cheapest next test is the separator of an explanation that is still
    # live: it says what evidence would move the question, and moving the
    # question is the work.
    for i in live[:1]:
        sep = str(i.get("separator") or "").strip()
        if sep:
            lines.append(f"能分开它的证据：{_short(sep, 160)}")
            break
    return lines


def _short(text: Any, limit: int = 110) -> str:
    """Trim a long field to its first clause.

    The separators are written for the record and run to several hundred
    characters. Injected, they eat the budget that the standing and the
    alignment check need, and the point of a separator - that an
    observation would tell two explanations apart - survives the first
    sentence.
    """
    t = " ".join(str(text or "").split())
    if len(t) <= limit:
        return t
    cut = t[:limit]
    for sep in ("。", "; ", "；", ". "):
        idx = cut.rfind(sep)
        if idx > limit * 0.5:
            return cut[: idx + len(sep)].rstrip()
    return cut.rstrip() + "…"


def _standing(topic: str) -> list[str]:
    """Where the project stands: the goal, the phase, the current task.

    These three sit above the evidence. The core question says what the
    project is about; the goal says what it is trying to get at; the
    current task says what is being done right now. An agent that sees only
    the current task will do it well and drift.
    """
    m = read_map(topic)
    lines: list[str] = []
    goal = str(m.get("goal") or "").strip()
    if goal:
        lines.append(f"课题目标：{goal}")
    phase = str(m.get("phase") or "").strip()
    if phase:
        lines.append(phase_thinking(phase))
    task = str(m.get("current_task") or "").strip()
    if task:
        lines.append(f"当前任务：{task}")
    # The narrative is what we currently believe and why it changed. It is
    # the answer to "where is this project", and it is the thing a review
    # compares the work against.
    narr = str(m.get("narrative") or "").strip()
    if narr:
        lines.append(f"当前判断（课题叙事）：{narr}")
    plan = str(m.get("plan") or "").strip()
    if plan:
        lines.append(f"下一步计划：{plan}")
    return lines


def review_demand(topic: str, review: dict[str, Any] | None = None) -> list[str]:
    """Ask for the review, when a review is due.

    A review that fires every turn is a nag and gets ignored; one that
    fires on a counter is a checkpoint. The counters are per topic: a
    drift that takes three turns to appear is not visible in any one of
    them, so the review has to look at the topic, not the turn.
    """
    r = review or read_review(topic)
    if r.get("paused"):
        return [
            "这个课题现在处于暂停状态：上一次全局审查判定工作已经偏离核心问题。",
            "先别继续检索。先想清楚：这个课题现在到底在回答什么、"
            "手上这些材料说明了什么、下一步要做什么，"
            "用 research_mode(action='set_state', ...) 把叙事和计划改掉，"
            "再回来检索。",
        ]
    return []


def _alignment(topic: str) -> list[str]:
    """Ask, every turn, whether the current task still serves the core
    question.

    Drift is the failure that no local check catches. Every claim in the
    ledger can be sourced, every inference can be within its premises, and
    the project can still have become a different project - a pile of
    well-supported facts about a question nobody is asking any more.

    So the alignment is stated on every turn, with both sides of it: the
    core question, and the current task. The agent is asked to notice the
    gap. Nothing is blocked; the point is that the gap is visible while
    there is still time to close it.
    """
    m = read_map(topic)
    core = str(m.get("core_question") or "").strip()
    task = str(m.get("current_task") or "").strip()
    goal = str(m.get("goal") or "").strip()
    if not task:
        return []
    if not core and not goal:
        return []
    # One line. The standing is already stated just above, so repeating it
    # here would spend the budget on a copy. What this line has to do is
    # put the task and the question next to each other and ask.
    side = f"（目标：{_short(goal, 60)}）" if goal else ""
    return [
        f"对齐检查：当前任务「{_short(task, 60)}」还在推进核心问题吗{side}？"
        f"若只是在解决更小的子问题，说出来，并说要不要调任务或换阶段。"
    ]


def build_research_block(topic: str, config: dict[str, Any] | None = None) -> str:
    """The text injected while a session is in research mode for `topic`."""
    m = read_map(topic)
    if not m:
        return f"[Research OS · {topic}]\n该课题还没有 research-map.json，先读它再回答。"

    hyp = [h for h in (m.get("hypotheses") or []) if str(h).strip()]
    open_q = [q for q in (m.get("open_questions") or []) if str(q).strip()]

    lines = [f"[Research OS · {topic}]"]
    lines.append(f"核心问题：{m.get('core_question', '')}")
    # Where the project stands comes before what is known. The goal, the
    # phase and the current task are what the turn is for; the evidence is
    # what the turn works with.
    lines.extend(_standing(topic))
    # The phase decides the shape of the answer, not only the way of
    # thinking about it. A model that gathers evidence while it should be
    # weighing explanations produces a list of findings with no position in
    # it, and a list of findings is what the reader cannot use.
    phase = str(m.get("phase") or "").strip()
    if phase:
        shape = phase_output(phase)
        if shape:
            lines.append(f"这轮要交出的东西：{shape}")
        gate = phase_gate(phase)
        if gate:
            lines.append(f"这轮不要做：{gate}")
    # The situation comes next: the state of play, not a list of things
    # someone wrote down once.
    lines.extend(_situation(topic))
    if not any(
        ln.startswith(("当前最佳解释", "候选解释", "受质疑", "能分开")) for ln in lines
    ) and hyp:
        lines.append("竞争解释：" + " ｜ ".join(hyp))
    if open_q:
        lines.append("待解问题：" + " ｜ ".join(open_q[:2]))
    # The alignment check: the drift that no local check catches.
    lines.extend(_alignment(topic))
    # The thinking moves come before the claim rules. A mode that leads with
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
    # The standing and the alignment check come first: they are what the
    # turn is for, and the rules are how to work on it. Losing the standing
    # to keep a rule inverts the block.
    priority = re.compile(
        r"^(\[Research OS|核心问题：|课题目标：|当前阶段：|当前任务：|对齐检查|"
        r"  课题目标：|  核心问题：|  当前任务：|  若当前任务|"
        r"当前最佳解释|候选解释|受质疑的解释|"
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
        if used + cost <= cap and not any(tool_line in k for k in kept):
            kept.append(line)
        elif used + cost > cap:
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
